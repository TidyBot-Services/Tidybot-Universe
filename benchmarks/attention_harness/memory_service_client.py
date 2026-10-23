"""Compatibility import for the independent Memory Service HTTP client."""

from attention_memory_service.memory_service_client import MemoryServiceClient as _Client


class MemoryServiceClient(_Client):
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

    def record_dev_v2_failed_check(self,**payload):
        if not getattr(self,'_importer_file',False):raise PermissionError('failed checks require importer key file')
        return self._call('POST','/dev-v2/failed-checks',payload,importer=True)

    def memory_events(self,*,after=0):
        if type(after) is not int or after<0:raise ValueError('nonnegative event cursor required')
        return self._call('GET',f'/dev-v2/memory-events?after={after}')['events']

__all__ = ["MemoryServiceClient"]
