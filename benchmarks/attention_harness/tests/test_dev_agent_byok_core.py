"""Secrets, owner isolation, pipes, scanning, and process hardening."""
import base64
import copy
import io
import json
import logging
import os
from pathlib import Path
import pickle
import secrets
import signal
import sqlite3
import subprocess
import sys
import traceback
from urllib.parse import quote
import pytest
from benchmarks.attention_harness.dev_agent_secret import Secret,scrub
from benchmarks.attention_harness.dev_agent_key_vault import ProviderKeyVault,RunKeys,launch_run_with_keys,encode_for_pipe
from benchmarks.attention_harness.dev_agent_provider import ProviderError
from benchmarks.attention_harness.canary_scan import scan


def canary():return Secret('sk-'+secrets.token_urlsafe(32))


def test_secret_and_scrub():
    key=canary();value=key.reveal()
    assert str(key)==repr(key)==format(key,'>30')=='<secret>'
    assert key==Secret(value) and key==value and key!=canary() and bool(key) and not Secret('')
    for call in (json.dumps,pickle.dumps,copy.copy,copy.deepcopy,hash):
        with pytest.raises(TypeError) as exc:call(key)
        assert not scan([key],roots=[],include_processes=False,extra_blobs=[str(exc.value)])
    stream=io.StringIO();handler=logging.StreamHandler(stream);logger=logging.getLogger('byok-secret');logger.addHandler(handler)
    try:logger.warning('credential %s %r',key,key)
    finally:logger.removeHandler(handler)
    try:raise ValueError(key)
    except ValueError:trace=traceback.format_exc()
    assert not scan([key],roots=[],include_processes=False,extra_blobs=[trace,stream.getvalue(),json.dumps(key,default=repr)])
    for text in (value,'Bearer '+value,'Incorrect API key provided: sk-xxxx****abcd','key: '+secrets.token_hex(20)):
        clean,changed=scrub(text,[key]);assert changed and '[REDACTED:key]' in clean
        assert not scan([key],roots=[],include_processes=False,extra_blobs=[clean])
    assert scrub(123,[])==('123',False)


def test_vault_lifecycle_and_references():
    now=[100];vault=ProviderKeyVault(clock=lambda:now[0]);a=canary();b=canary()
    vault.put('one','x','openai',a);vault.put('one','y','deepseek',b);vault.put('two','x','openai',b)
    assert vault.list('one')[0]['expires_at']==86500
    role={'dev':dict(credential='x',provider='openai',model='m')}
    taken=vault.take_for_run('one',role)
    assert set(taken._keys)=={'x'} and taken._keys['x']==a
    assert vault.take_for_run('two',role)._keys['x']==b
    assert len(vault.list('one'))==2 and not vault.list('absent')
    now[0]=200;vault.take_for_run('one',role);assert vault.list('one')[0]['expires_at']==86500
    vault.put('one','x','openai',b);assert vault.list('one')[0]['expires_at']==86600
    now[0]=86501;vault.purge_expired();assert len(vault.list('one'))==1 and not vault.list('two')
    now[0]=86600
    with pytest.raises(ProviderError,match='dev') as exc:vault.take_for_run('one',role)
    assert exc.value.code=='provider_key_missing'
    vault.put('one','x','openai',a);vault.put('one','y','openai',b)
    assert vault.clear('one','x')==1 and vault.clear('one')==1
    vault.put('two','x','openai',b);vault.clear_all();assert not vault.list('two')
    for item in (vault,taken):
        with pytest.raises(TypeError):pickle.dumps(item)


@pytest.mark.parametrize('name',['','UPPER','a_b','a'*33,'a/b'])
def test_credential_names(name):
    with pytest.raises(ProviderError):ProviderKeyVault().put('o',name,'openai',canary())


@pytest.mark.parametrize('ttl',[0,-1,float('nan'),float('inf'),'24'])
def test_invalid_ttl(ttl):
    with pytest.raises(ProviderError):ProviderKeyVault().put('o','x','openai',canary(),ttl=ttl)


def test_pipe_process_hardening_and_no_environment(tmp_path):
    key=canary();keys=RunKeys({'x':key},{'dev':dict(credential='x',provider='openai',model='m')})
    code='''import os,sys,json,resource
from benchmarks.attention_harness.dev_agent_key_vault import install_run_keys_from_fd,run_key,run_role
fd=int(sys.argv[sys.argv.index('--key-fd')+1]);install_run_keys_from_fd(fd)
try:os.fstat(fd);closed=False
except OSError:closed=True
assert closed and resource.getrlimit(resource.RLIMIT_CORE)==(0,0)
assert run_role('dev')['credential']=='x'
assert run_key('x')
print('pipe-ok')
'''
    process=launch_run_with_keys([sys.executable,'-B','-c',code],keys,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    out,err=process.communicate(timeout=15)
    assert process.returncode==0 and out==b'pipe-ok\n' and not err
    assert not scan([key],roots=[tmp_path],extra_blobs=[out,err,json.dumps(process.args)])
    plain=json.dumps({'x':key.reveal()}).encode()
    code="from benchmarks.attention_harness.dev_agent_key_vault import install_run_keys_from_stdin,run_key;install_run_keys_from_stdin();assert run_key('x')"
    p=subprocess.run([sys.executable,'-B','-c',code],input=plain,capture_output=True)
    assert p.returncode==0 and not p.stdout and not p.stderr


@pytest.mark.parametrize('signum',[signal.SIGTERM,signal.SIGINT])
def test_signals_clear_and_preserve_exit(signum):
    code='''import os,signal,sys
from benchmarks.attention_harness.dev_agent_key_vault import ProviderKeyVault
from benchmarks.attention_harness.dev_agent_secret import Secret
import secrets
sig=int(sys.argv[1])
def previous(*args):
    assert not vault.list('o')
    raise SystemExit(23)
signal.signal(sig,previous)
vault=ProviderKeyVault();vault.put('o','x','openai',Secret(secrets.token_urlsafe()))
os.kill(os.getpid(),sig)
'''
    p=subprocess.run([sys.executable,'-B','-c',code,str(signum)],capture_output=True)
    assert p.returncode==23 and not p.stdout and not p.stderr
    code="from benchmarks.attention_harness.dev_agent_key_vault import ProviderKeyVault;import os,signal;v=ProviderKeyVault();os.kill(os.getpid(),signal.SIGTERM)"
    p=subprocess.run([sys.executable,'-B','-c',code],capture_output=True)
    assert p.returncode==-signal.SIGTERM


def test_scanner_positive_variants_and_sqlite(tmp_path):
    key=Secret(secrets.token_urlsafe(32)+' /"\\é');raw=key.reveal().encode()
    blobs={'raw':raw,'json':json.dumps(key.reveal())[1:-1].encode(),'url':quote(key.reveal(),safe='').encode(),
           'hex':raw.hex().encode(),'prefix':raw[:12],'suffix':raw[-12:]}
    for i in range(3):blobs['b64-'+str(i)]=base64.b64encode(b'x'*i+raw+b'end')
    for name,blob in blobs.items():
        assert scan([key],roots=[],include_processes=False,extra_blobs={name:blob})
    path=tmp_path/'intentional-leak';path.write_bytes(raw)
    dbpath=tmp_path/'db.sqlite'
    with sqlite3.connect(dbpath) as db:
        db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE values_to_scan (value TEXT)')
        db.execute('INSERT INTO values_to_scan VALUES (?)',(key.reveal(),));db.commit()
        hits=scan([key],roots=[tmp_path],sqlite_paths=[dbpath],include_processes=False)
        assert any(h.where==str(path) for h in hits) and any(h.where.endswith(':iterdump') for h in hits)
        assert all(set(vars(h))=={'where','variant'} for h in hits)
    db.close();path.unlink();dbpath.unlink()
    assert not scan([key],roots=[tmp_path],include_processes=False)


def test_scanner_process_positive_control():
    key=canary()
    process=subprocess.Popen([sys.executable,'-B','-c','import sys;sys.stdin.read()'],stdin=subprocess.PIPE,
                             env={**os.environ,'BYOK_SCANNER_NEGATIVE_CONTROL':key.reveal()})
    try:
        hits=scan([key],roots=[])
        assert any(h.where=='/proc/%s/environ'%process.pid for h in hits)
    finally:process.communicate(timeout=10)


def test_json_escaped_error_key():
    key=Secret(secrets.token_urlsafe(32)+'"\\é')
    text,changed=scrub(json.dumps(dict(error=key.reveal())),[key])
    assert changed and '[REDACTED:key]' in text
    assert not scan([key],roots=[],include_processes=False,extra_blobs=[text])


def test_export_refuses_key_before_writing(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from benchmarks.attention_harness import dev_agent_key_vault as vault
    from benchmarks.attention_harness.dev_agent_export import Exporter
    key=canary();monkeypatch.setattr(vault,'_RUN_KEYS',{'x':key})
    exporter=object.__new__(Exporter)
    exporter.workspace=SimpleNamespace(private=tmp_path,canonical=tmp_path)
    exporter.pending={'request':dict(row=dict(summary=key.reveal()))}
    with pytest.raises(ProviderError,match='export refused') as exc:
        exporter.settle(dict(op='submit',request_id='request'))
    assert exc.value.code=='provider_secret_in_export' and not list(tmp_path.iterdir())
    vault.assert_run_clean(roots=[tmp_path],extra_blobs=[b'clean public data'])
