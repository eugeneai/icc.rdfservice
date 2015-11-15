from zope.interface import implementer, Interface, Attribute
from icc.rdfservice.interfaces import IGraph, ITripleStore, IRDFService, IRDFStorage
from zope.component import getUtility, getGlobalSiteManager # , classImplements
import rdflib, rdflib.graph
import os, os.path
from rdflib import Literal, BNode, URIRef
import datetime
from collections import OrderedDict
import logging
from icc.rdfservice.namespace import *
import pengines
import random

logger=logging.getLogger('icc.cellula')


#classImplements(rdflib.graph.Graph, IGraph)
#classImplements(rdflib.graph.QuotedGraph, IGraph)

#classImplements(rdflib.graph.ConjunctiveGraph, IGraph)
#classImplements(rdflib.graph.DataSet, IGraph)
#classImplements(rdflib.graph.ReadOnlyGraphAggregate, IGraph)


@implementer(IRDFService)
class RDFService(object):
    """Creates World of graphs.
    """
    def __init__(self):
        self.initialize()

    def initialize(self):
        """Create graphs and load then with intial data
        if necessary."""

        config=getUtility(Interface, name='configuration')


        self.storages={}
        stores_descr=config['rdf_storages']
        for sto in stores_descr['all'].split(','):
            sto=sto.strip()
            self.init_storage(sto)

        graphs_descr=config['graphs']
        self.graphs=OrderedDict()
        self.ns_man=None

        all_descr=graphs_descr['all']
        for g in all_descr.split(','):
            g=g.strip()
            self.init_graph(g)

    def init_storage(self, storage):
        config=getUtility(Interface, name='configuration')
        storage_descr=config['rdf_storage_'+storage]

        data_dir=storage_descr.get('data_dir', None)
        data_file=storage_descr.get('data_file', storage)

        driver=storage_descr.get('driver', 'default')

        if data_dir != None:
            data_filepath=os.path.join(data_dir, data_file)
        else:
            data_filepath=None

        self.storages[storage]=(driver, data_filepath)

    def init_graph(self, name):
        config=getUtility(Interface, name='configuration')
        graph_descr=config['graph_'+name]
        contains=graph_descr.get('contains', None)
        identifier=graph_descr.get('id', name)
        storage=graph_descr['storage']
        load=graph_descr.get('load_from', None)

        sto_driver, sto_filepath=self.storages[storage]

        if contains == None:
            graph=rdflib.graph.Graph(
                store=sto_driver,
                identifier=identifier,
                namespace_manager=self.ns_man
            )
            if sto_filepath != None:
                graph.open(sto_filepath,create=True)

        else:
            if sto_driver != 'default':
                graph=rdflib.graph.ConjunctiveGraph(sto_driver, identifier=identifier)
                if sto_filepath != None:
                    graph.open(sto_filepath,create=True)
            else:
                graph=rdflib.graph.Dataset(sto_driver)
                for cg in contains.split(','):
                    cg=cg.strip()
                    g=self.graphs[cg]
                    graph.add_graph(g)

        if len(graph)==0 and load != None:
            try:
                graph.parse(load) # FIXME slashes in Windows
                graph.commit()
            except IOError:
                logger.warning ("Cannot load graph from URI:" + load)

        for nk,nv in NAMESPACES.items():
            graph.bind(nk,nv)

        if name=='ns':
            self.ns_man=graph

        self.graphs[name]=graph
        GSM=getGlobalSiteManager()
        GSM.registerUtility(graph, IGraph, name=name)

    def __del__(self):
        logger.info ("Terminating graph storage ....")
        graphs=self.graphs.keys()
        graphs.reverse()
        for gk in graphs:
            g=self.graphs[gk]
            g.commit()
            g.close(True)

class ReadOnlyRDFService(RDFService):
    pass

@implementer(IRDFStorage)
class RDFStorage(object):
    graph_name=None

    def store(self, things):
        """Store things represented as mapping in the
        graph as triples.
        """

        g=self.getUtility(IGraph, name=self.graph_name)
        for k,v in things.items():
            for triple in self.convert(k,v, things):
                s,p,o=triple[:3]
                rest=triple[3:]
                if None in [s,p,o]:
                    continue
                else:
                    try:
                        g.add((s,p,o))
                    except AssertionError as e:
                        logger.error('Assertion %s for triple %s came from: %s.' % (e, (s,p,o), rest))

        g.commit()

    def convert(self, key, values, things):
        """Convert each value from values related
        to key.
        """
        if type(values) in [list, tuple]:
            # remove duplicates
            values=set(values)
            for v in values:
                yield from self.convert_one(key, v, things)
        else:
            yield from self.convert_one(key, values, things)

    def convert_one(self, key, value, things):
        """Convert one key-value pair in context of other things.

        Arguments:
        - `key`:
        - `value`:
        - `things`:
        """
        raise RuntimeError ("implemented by subclass.")

@implementer(IRDFStorage)
class ClioPatria(RDFStorage):
    graph_name = 'document'

    def __init__(self):
        self.config=getUtility(Interface, 'configuration')['pengines']
        self.url=self.config['URL']

    def store(self, things):
        """Store things represented as mapping in the
        graph as triples.
        """

        def _(s):
            return "'{}'".format(str(s))

        g=self.graph_name
        PengQ=[]
        for k,v in things.items():
            for triple in self.convert(k,v, things):
                s,p,o=triple[:3]
                rest=triple[3:]
                if None in [s,p,o]:
                    continue
                else:
                    ps=_(s)
                    pp=_(p)
                    if type(o)==Literal:
                        lang=o._language
                        # xor
                        datat=o._datatype    #uriref
                        val=o._value
                        if lang != None:
                            lang=_(lang)
                        if datat != None:
                            dt=_(datat)
                        if lang == None and datat==None:
                            po=_(o)
                        elif lang != None:
                            po="lang({},{})".format(lang,_(o))
                        elif type(val) in [int,float]: # datat!=None
                            po="type({},{})".format(dt, val)
                        else:
                            po="type({},'{}')".format(dt, val)
                        po="literal({})".format(po)
                    else:
                        po=_(o)
                    Q="icc:assert({},{},{},document)".format(ps,pp,po)
                    PengQ.append(Q)
        if len(PengQ)==0:
            return
        PengQ.append('icc:flush')
        pid=str(random.randint(1,2015**2))
        src_text="p{}:-\n{}.".format(pid,',\t\n'.join(PengQ))
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("META SOURCE:"+src_text)
        for _ in self.query(src_text=src_text, query="p{}".format(pid)):
            return True
        return False

    def query(self, query=None, **kwargs):
        if not query:
            raise ValueError('empty query')
        peng=pengines.Pengine(url=self.url)
        rc=peng.create(**kwargs)
        print ("Query:", query)
        yield from peng.query(query=query)

    def current_user(self):
        """Return ID of current user."""
        Id="mailto:eugeneai@npir.ru"
        for _ in self.query(query="icc:person('{0}',E,ensure_exists)".format(Id)):
            return (Id, _['E'])
        raise RuntimeError('cannot instantiate current person')

class DocMetadataStorage(ClioPatria): # FIXME make adapter, a configurated one.
    graph_name='doc'
    KEYS={
        "id":"_id"
    }

    NPM_FILTER=set([
        "nco:fullname",
        "nfo:tableOfContents",
        "nco:creator",
        ])

    def convert_one(self, k, v, ths):
        if k in self.KEYS:
            method_name = self.KEYS[k]
            method=getattr(self, method_name)
            yield from method(v, ths)
        else:
            logger.debug ("\n========================")
            logger.debug ("CNV: " + str(k) + " -> " + str(v)[:100])
            yield (None, None, None, str(k), str(v)[:100])

    def _id(self, hash_id, ths):
        anno = BNode()

        # Anotation target
        target = BNode()
        yield (anno, RDF['type'], OA['Annotation'])
        yield (anno, OA['motivatedBy'], OA['describing'])
        yield (anno, OA['hasTarget'], target)
        yield (target, NAO['identifier'], Literal(hash_id))
        yield from self.p("Content-Type", target, NMO['mimeType'], ths)
        if "rdf:type" in ths:
            yield from self.rdf(target, ths, filter_out=self.NPM_FILTER)
        else:
            yield (target, RDF['type'], NFO['Document'])
        yield from self.p("File-Name", target, NFO['fileName'], ths)

        # Annotation itself
        if 'text-id' in ths:
            body = BNode()
            yield (anno, OA['hasBody'], body)
            yield (body, RDF['type'], CNT['ContextAsText'])
            yield (body, NAO['identifier'], Literal(ths['text-id']))
            mt=None
            html=plain=False
            rdf_a=NFO.HtmlDocument
            #recoll-meta
            if "text|mimetype" in ths:
                mt=ths['text|mimetype']
            else: # let's guess. FIXME CPU consuming and very stupid.
                if ths['text-body'].upper().find("</BODY") >= 0:
                    mt="text/html"
                else:
                    mt="text/plain"
            html=mt.endswith("html")
            plain=mt.endswith("plain")
            if html:
                yield (body, RDF['type'], NFO['HtmlDocument'])
            if plain:
                yield (body, RDF['type'], NFO['PlainTextDocument'])
            yield (body, NMO['mimeType'], Literal(mt))


        # User
        (user_id, user)=self.current_user()
        user=BNode(user)
        yield (anno, OA['annotator'], user)
        #yield (user, RDF['type'], FOAF['Person'])
        #yield (user, NAO['identifier'], Literal(ths["user-id"]))
        utcnow=datetime.datetime.utcnow()
        # ts=utcnow.strftime("%Y-%m-%d%Z:%H:%M:%S")
        ts=utcnow.strftime("%Y-%m-%dT%H:%M:%SZ")
        yield (anno, OA['annotatedAt'], Literal(ts,datatype=XSD.dateTime))

    def p(self, key, s, o, ths, cls=Literal):
        if key in ths:
            yield (s, o, cls(ths[key]), key)
        else:
            yield (None, None, None, key)

    def rdf(self, s, ths, filter_out=None):
        """Generate all found rdf relations,
        except mentioned in thw filter_out set."""
        keys=list(ths.keys())
        # logger.debug ("Keys: " + repr(keys))
        for key in keys:
            okey=key
            val=ths[key]
            oval=val
            logger.debug ("->>> " + key + " : " + str(val)[:30])
            ks=key.split(":", maxsplit=1)
            if len(ks)!=2:
                continue
            if key in filter_out:
                continue
            key=ou(key)
            if type(val) in [int, float]:
                yield (s, key, Literal(val), okey, str(oval)[:100])
                continue
            if val.startswith('"') and val.endswith('"'):
                val=val.strip('"')
                yield (s, key, Literal(val), okey, str(oval)[:100])
                continue
            if val.startswith("'") and val.endswith("'"):
                val=val.strip("'")
                yield (s, key, Literal(val), okey, str(oval)[:100])
                continue
            vs=val.split(":", maxsplit=1)
            if len(vs)!=2:
                logger.warning ("Strange object value: " + str(val) + " for property: " + str(key))
                continue
            yield (s, key, ou(val), okey, str(oval)[:100])



def ou(lit):
    """Converts string xxx:yyy to global
    space object XXX.yyy"""

    #print ("Got lit: " + str(lit))
    if lit.find(',')>=0:
        return None
    if lit.find(' ')>=0:
        return None
    rc=lit.split(":")
    if len(rc)>2:
        return None
    ns, ent = rc
    try:
        ns=NAMESPACES[ns]
    except KeyError:
        return None
    try:
        rc=ns[ent]
    except Exception:
        rc=None
    return rc
