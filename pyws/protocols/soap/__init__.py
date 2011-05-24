import re

from StringIO import StringIO

from lxml import etree as et

from pyws.errors import BadRequest
from pyws.functions.args import List, Dict, String
from pyws.response import Response
from pyws.protocols.base import Protocol

from utils import *
from wsdl import WsdlGenerator

TAG_NAME_RE = re.compile('{(.*?)}(.*)')
ENCODING = 'utf-8'

def get_element_name(el):
    name = el.tag
    mo = TAG_NAME_RE.search(name)
    if mo:
        return mo.group(1), mo.group(2)
    return None, name

NIL = '{http://www.w3.org/2001/XMLSchema-instance}nil'

def xml2obj(xml, schema):
    children = xml.getchildren()
    if not children:
        if xml.text is None:
            result = None
        else:
            result = unicode(xml.text)
    elif issubclass(schema, List):
        result = []
        for child in children:
            result.append(xml2obj(child, schema.element_type))
    elif issubclass(schema, Dict):
        result = {}
        schema = dict((field.name, field.type) for field in schema.fields)
        for child in children:
            name = get_element_name(child)[1]
            obj = xml2obj(child, schema[name])
            if name not in result:
                result[name] = obj
            else:
                if not isinstance(result[name], list):
                    result[name] = [result[name]]
                result[name].append(obj)
    else:
        raise BadRequest('Couldn\'t decode XML')
    return result

def obj2xml(root, contents, namespace=None):
    kwargs = namespace and {'namespace': namespace} or {}
    if isinstance(contents, (list, tuple)):
        for item in contents:
            element = et.SubElement(root, 'item', **kwargs)
            obj2xml(element, item, namespace)
    elif isinstance(contents, dict):
        for name, item in contents.iteritems():
            element = et.SubElement(root, name, **kwargs)
            obj2xml(element, item, namespace)
    elif contents is not None:
        root.text = unicode(contents)
    elif contents is None:
        root.set(NIL, 'true')
    return root


class SoapProtocol(Protocol):

    namespaces = {'se': SOAP_ENV_NS}

    def __init__(self, service_name, tns_prefix, *args, **kwargs):
        super(SoapProtocol, self).__init__(*args, **kwargs)
        self.service_name = service_name
        self.tns_prefix   = tns_prefix

    def get_function(self, request):

        if request.tail == 'wsdl':
            return self.get_wsdl

        request.xml = et.parse(StringIO(request.text.encode(ENCODING)))

        env = request.xml.xpath('/se:Envelope', namespaces=self.namespaces)

        if len(env) == 0:
            raise BadRequest('No {%s}Envelope element.' % SOAP_ENV_NS)
        env = env[0]

        body = env.xpath('./se:Body', namespaces=self.namespaces)

        if len(body) == 0:
            raise BadRequest('No {%s}Body element.' % SOAP_ENV_NS)
        if len(body) > 1:
            raise BadRequest(
                'There must be only one {%s}Body element.' % SOAP_ENV_NS)
        body = body[0]

        func = body.getchildren()
        if len(env) == 0:
            raise BadRequest(
                '{%s}Body element has no child elements.' % SOAP_ENV_NS)
        if len(env) > 1:
            raise BadRequest('{%s}Body element '
                'has more than one child element.' % SOAP_ENV_NS)
        func = func[0]

        request.func_xml = func

        func_name = get_element_name(func)[1]
        if func_name.endswith('_request'):
            func_name = func_name[:-8]

        return func_name

    def get_arguments(self, request, arguments):
        return xml2obj(request.func_xml, arguments)

    def get_response(self, name, result):

        result = obj2xml(et.Element(
            name + '_response', namespace=self.tns_prefix), {'result': result})

        body = et.Element(soap_env_name('Body'), nsmap=self.namespaces)
        body.append(result)

        xml = et.Element(soap_env_name('Envelope'), nsmap=self.namespaces)
        xml.append(body)

        return Response(et.tostring(xml,
            encoding=ENCODING, pretty_print=True, xml_declaration=True))

    def get_error_response(self, error):

        error = self.get_error(error)

        fault = et.Element(soap_env_name('Fault'), nsmap=self.namespaces)
        faultcode = et.SubElement(fault, 'faultcode')
        faultcode.text = 'se:%s' % error['type']
        faultstring = et.SubElement(fault, 'faultstring')
        faultstring.text = error['message']
        fault.append(obj2xml(et.Element('detail'), error))

        body = et.Element(soap_env_name('Body'), nsmap=self.namespaces)
        body.append(fault)

        xml = et.Element(soap_env_name('Envelope'), nsmap=self.namespaces)
        xml.append(body)

        return Response(et.tostring(xml,
            encoding=ENCODING, pretty_print=True, xml_declaration=True))

    def get_wsdl(self, server, request):
        return Response(WsdlGenerator(server,
            self.service_name, self.tns_prefix, ENCODING).get_wsdl())