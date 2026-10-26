"""Compatibility import for the independent Memory Service HTTP client."""

from attention_memory_service.memory_service_client import MemoryServiceClient as _Client
from copy import copy


def validate_evaluate_timeout(value=1800):
    if type(value) not in (int,float) or not 0<value<float('inf'):
        raise ValueError('evaluate_timeout_seconds must be a finite positive number')
    return value


class MemoryServiceClient(_Client):
    def __init__(self,*args,evaluate_timeout_seconds=1800,**kwargs):
        self.evaluate_timeout_seconds=validate_evaluate_timeout(evaluate_timeout_seconds)
        super().__init__(*args,**kwargs)

    def _call(self,method,path,payload=None,**kwargs):
        client=self
        if path.startswith('/memories/') and (method,path.rsplit('/',1)[-1]) in {('POST','evaluate'),('GET','decision')}:
            # The runner and monitor can share this client. Never temporarily
            # mutate its ordinary timeout while evidence verification blocks.
            client=copy(self)
            client.timeout=self.evaluate_timeout_seconds
        return _Client._call(client,method,path,payload,**kwargs)

    @classmethod
    def offline(cls,base_url,*,key_file,**kwargs):
        from .memory_auto_import import read_keys
        keys=read_keys(key_file)
        if keys['ATTENTION_MEMORY_API_KEY']==keys['ATTENTION_MEMORY_IMPORTER_KEY']:raise PermissionError('distinct importer capability required')
        client=cls(base_url,api_key=keys['ATTENTION_MEMORY_API_KEY'],importer_key=keys['ATTENTION_MEMORY_IMPORTER_KEY'],**kwargs)
        client._importer_file=True
        return client

    def register_dev_v2_catalog(self,**payload):
        if not getattr(self,'_importer_file',False):raise PermissionError('catalog registration requires importer key file')
        return self._call('POST','/dev-v2/catalogs',payload,importer=True)

    def validation_queue(self):return self._call('GET','/dev-v2/validation-queue')['items']

    def cross_seed_queue(self):return self._call('GET','/dev-v2/cross-seed/queue')['queue']

    def cross_seed_status(self,*,memory_id):
        from urllib.parse import urlencode
        return self._call('GET','/dev-v2/cross-seed/status?'+urlencode({'memory_id':memory_id}))

    def cross_seed_write(self,route,**payload):
        if route not in {'probes','plans','pairs','failed-checks'}:raise ValueError('unknown cross-seed route')
        if not getattr(self,'_importer_file',False):raise PermissionError('cross-seed writes require importer key file')
        return self._call('POST','/dev-v2/cross-seed/'+route,payload,importer=True)

    def record_dev_v2_failed_check(self,**payload):
        if not getattr(self,'_importer_file',False):raise PermissionError('failed checks require importer key file')
        return self._call('POST','/dev-v2/failed-checks',payload,importer=True)

    def memory_events(self,*,after=0):
        if type(after) is not int or after<0:raise ValueError('nonnegative event cursor required')
        return self._call('GET',f'/dev-v2/memory-events?after={after}')['events']

__all__ = ["MemoryServiceClient"]
