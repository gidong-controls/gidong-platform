"""Host-owned fixed read-only vendor lifecycle diagnostic; no public execution surface."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import ntpath
import os
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .agent_protocol import native_checkpoint_boundary

SCHEMA='controlstudio-vendor-process-diagnostic/v1'
_NAMES=frozenset({'simulator.exe','xgservice.exe','xgprojectserver.exe'})
_DURATION_SECONDS=600
_MAX_RECORDS=512
_MAX_STDOUT_BYTES=1024*1024
_MAX_LINE_BYTES=512*1024
_MAX_REPORT_BYTES=2*1024*1024
_READY_TIMEOUT_SECONDS=10.0
_DRAIN_TIMEOUT_SECONDS=5.0
_CLEANUP_TIMEOUT_SECONDS=2.0
_LOG=logging.getLogger(__name__)
_PS_SOURCE=r'''#requires -Version 5.1
# Root executes this observer separately. It never starts or controls the product.
$ErrorActionPreference = 'Stop'
if (-not (Get-Command Register-WmiEvent -ErrorAction SilentlyContinue)) {
    throw 'Windows PowerShell 5.1 with Register-WmiEvent is required.'
}
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$DurationSeconds=600
$stopReader=[IO.StreamReader]::new([Console]::OpenStandardInput())
$stopSignal=$stopReader.ReadLineAsync()
function Send-ProtocolFrame($Frame) {
    $Frame['schema']='controlstudio-vendor-process-diagnostic/v1'
    [Console]::Out.WriteLine(($Frame | ConvertTo-Json -Compress -Depth 6))
    [Console]::Out.Flush()
}
$targetNames = @('Simulator.exe', 'XGService.exe', 'XGProjectServer.exe')
$nameFilter = "ProcessName='Simulator.exe' OR ProcessName='XGService.exe' OR ProcessName='XGProjectServer.exe'"
$snapshotFilter = "Name='Simulator.exe' OR Name='XGService.exe' OR Name='XGProjectServer.exe'"
$tag = 'ControlStudio.ProcessObserver.' + [Guid]::NewGuid().ToString('N')
$sourceIds = @(($tag + '.start'), ($tag + '.stop'))
$registeredIds = New-Object 'System.Collections.Generic.List[string]'
$records = New-Object 'System.Collections.Generic.List[object]'
$identities = @{}
$timer = $null
$report = [ordered]@{
    schema = 'simulator-process-lifecycle-observation/v1'
    target_names = $targetNames
    requested_duration_seconds = $DurationSeconds
    ready_utc = $null
    ended_utc = $null
    collection_status = 'initializing'
    record_limit = 512
    record_limit_reached = $false
    query_failures = 0
    cleanup_failures = 0
    error_type = $null
    records = $null
    limits = 'WMI delivery completeness is not certified. Unavailable executable/creation data stays null. Event UTC is provider event time, not process creation time. Only Simulator.exe start-event identity may include CommandLine (Windows 32767-character bound); baseline/service arguments remain null. No token, SID, credential, environment or UI data is queried.'
}

function Read-ProcessIdentity {
    param([int]$ObservedPid, [string]$ExpectedName, [DateTime]$EventUtc)
    if ($null -ne $script:timer -and $DurationSeconds - $script:timer.Elapsed.TotalSeconds -lt 2) { return $null }
    # Only Simulator.exe receives the explicitly approved bounded argv query.
    try {
        $fields = if ($ExpectedName -ieq 'Simulator.exe') { 'ProcessId,ParentProcessId,CreationDate,ExecutablePath,Name,CommandLine' } else { 'ProcessId,ParentProcessId,CreationDate,ExecutablePath,Name' }
        $query = 'SELECT ' + $fields + ' FROM Win32_Process WHERE ProcessId=' + $ObservedPid
        $rows = @(Get-CimInstance -Namespace root/cimv2 -Query $query -OperationTimeoutSec 2)
        if ($rows.Count -ne 1 -or $rows[0].Name -ine $ExpectedName) { return $null }
        $created = $rows[0].CreationDate
        if ($created -isnot [DateTime]) { return $null }
        $created = $created.ToUniversalTime()
        # A process created after this event is a reused PID, not its subject.
        if ($created -gt $EventUtc) { return $null }
        return [pscustomobject]@{
            process_name = [string]$rows[0].Name
            pid = [int]$rows[0].ProcessId
            ppid = [int]$rows[0].ParentProcessId
            creation_time_utc = $created.ToString('o')
            executable_path = $rows[0].ExecutablePath
            command_line = $(if ($ExpectedName -ieq 'Simulator.exe' -and $null -ne $rows[0].CommandLine -and ([string]$rows[0].CommandLine).Length -le 32767) { [string]$rows[0].CommandLine } else { $null })
        }
    } catch {
        $script:report.query_failures++
        return $null
    }
}

function Add-IdentityRecord {
    param([string]$Kind, [DateTime]$EventUtc, [string]$ProcessName, [int]$ObservedPid, [int]$ParentPid, $Identity, $ExitStatus = $null)
    if ($script:records.Count -ge 512) {
        $script:report.record_limit_reached = $true
        return
    }
    $created = $null
    $executable = $null
    if ($null -ne $Identity) {
        $created = $Identity.creation_time_utc
        $executable = $Identity.executable_path
    }
    $record=[ordered]@{
        event = $Kind
        event_time_utc = $EventUtc.ToString('o')
        process_name = $ProcessName
        pid = $ObservedPid
        ppid = $ParentPid
        exit_status = $(if ($Kind -eq 'stop') { $ExitStatus } else { $null })
        creation_time_utc = $created
        executable_path = $executable
        command_line = $(if ($Kind -eq 'start' -and $null -ne $Identity -and $ProcessName -ieq 'Simulator.exe') { $Identity.command_line } else { $null })
    }
    $script:records.Add([pscustomobject]$record)
    Send-ProtocolFrame $record
}

function Drain-OwnedEvents {
    $pending = @(
        foreach ($source in $script:sourceIds) {
            Get-Event -SourceIdentifier $source -ErrorAction SilentlyContinue
        }
    ) | Sort-Object { [UInt64]$_.SourceEventArgs.NewEvent.TIME_CREATED }
    foreach ($item in $pending) {
        if ($script:records.Count -ge 512) { $script:report.record_limit_reached = $true; break }
        try {
            $nativeEvent = $item.SourceEventArgs.NewEvent
            $name = [string]$nativeEvent.ProcessName
            if ($script:targetNames -inotcontains $name) { continue }
            $observedPid = [int]$nativeEvent.ProcessID
            $parentPid = [int]$nativeEvent.ParentProcessID
            $eventUtc = [DateTime]::FromFileTimeUtc([Int64]$nativeEvent.TIME_CREATED)
            if ($item.SourceIdentifier -eq $script:sourceIds[0]) {
                $identity = Read-ProcessIdentity -ObservedPid $observedPid -ExpectedName $name -EventUtc $eventUtc
                $script:identities[$observedPid] = $identity
                Add-IdentityRecord -Kind 'start' -EventUtc $eventUtc -ProcessName $name -ObservedPid $observedPid -ParentPid $parentPid -Identity $identity
            } else {
                # The terminated PID is never queried: it may already be reused.
                $identity = $script:identities[$observedPid]
                if ($null -ne $identity -and ([DateTime]$identity.creation_time_utc -gt $eventUtc -or $identity.ppid -ne $parentPid -or $identity.process_name -ine $name)) {
                    $identity = $null
                }
                $exitStatus = $null
                if ($null -ne $nativeEvent.ExitStatus) { $exitStatus = [uint32]$nativeEvent.ExitStatus }
                Add-IdentityRecord -Kind 'stop' -EventUtc $eventUtc -ProcessName $name -ObservedPid $observedPid -ParentPid $parentPid -Identity $identity -ExitStatus $exitStatus
                $script:identities.Remove($observedPid)
            }
        } finally {
            Remove-Event -EventIdentifier $item.EventIdentifier -ErrorAction SilentlyContinue
        }
    }
}

try {
    foreach ($kind in @('start', 'stop')) {
        $class = if ($kind -eq 'start') { 'Win32_ProcessStartTrace' } else { 'Win32_ProcessStopTrace' }
        $source = $tag + '.' + $kind
        $fields = 'ProcessID,ParentProcessID,ProcessName,TIME_CREATED'
        if ($kind -eq 'stop') { $fields += ',ExitStatus' }
        $query = 'SELECT ' + $fields + ' FROM ' + $class + ' WHERE ' + $nameFilter
        $null = Register-WmiEvent -Namespace root/cimv2 -Query $query -SourceIdentifier $source
        $registeredIds.Add($source)
    }
    # Subscribe before the snapshot so a process crossing startup is observable.
    $snapshotQuery = 'SELECT ProcessId,ParentProcessId,CreationDate,ExecutablePath,Name FROM Win32_Process WHERE ' + $snapshotFilter
    foreach ($process in @(Get-CimInstance -Namespace root/cimv2 -Query $snapshotQuery -OperationTimeoutSec 2)) {
        if ($targetNames -inotcontains [string]$process.Name) { continue }
        $identity = $null
        if ($process.CreationDate -is [DateTime]) {
            $identity = [pscustomobject]@{
                process_name = [string]$process.Name
                pid = [int]$process.ProcessId
                ppid = [int]$process.ParentProcessId
                creation_time_utc = $process.CreationDate.ToUniversalTime().ToString('o')
                executable_path = $process.ExecutablePath
            }
        }
        $identities[[int]$process.ProcessId] = $identity
        Add-IdentityRecord -Kind 'baseline' -EventUtc ([DateTime]::UtcNow) -ProcessName ([string]$process.Name) -ObservedPid ([int]$process.ProcessId) -ParentPid ([int]$process.ParentProcessId) -Identity $identity
    }
    $report.ready_utc = [DateTime]::UtcNow.ToString('o')
    $report.collection_status = 'collecting'
    Send-ProtocolFrame ([ordered]@{event='ready';ready_utc=$report.ready_utc})
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt $DurationSeconds -and -not $report.record_limit_reached -and -not $stopSignal.IsCompleted) {
        Drain-OwnedEvents
        if ($timer.Elapsed.TotalSeconds -lt $DurationSeconds -and -not $report.record_limit_reached) {
            $null = Wait-Event -SourceIdentifier $sourceIds[0] -Timeout 1
        }
    }
    Drain-OwnedEvents
    $report.collection_status = 'observation_finished'
} catch {
    $report.collection_status = 'collection_failed'
    $report.error_type = $_.Exception.GetType().FullName
} finally {
    foreach ($source in $registeredIds) {
        try { Unregister-Event -SourceIdentifier $source -ErrorAction Stop }
        catch { $report.cleanup_failures++ }
        Get-Event -SourceIdentifier $source -ErrorAction SilentlyContinue | ForEach-Object {
            Remove-Event -EventIdentifier $_.EventIdentifier -ErrorAction SilentlyContinue
        }
    }
    $report.ended_utc = [DateTime]::UtcNow.ToString('o')
    Send-ProtocolFrame ([ordered]@{event='finished';ended_utc=$report.ended_utc;
        collection_status=$report.collection_status;record_limit_reached=$report.record_limit_reached;
        query_failures=$report.query_failures;cleanup_failures=$report.cleanup_failures;error_type=$report.error_type;
        stopped_by_owner=$stopSignal.IsCompleted})
}
'''

def _external_windows_environment(environment: dict[str, str], bundle: str) -> dict[str, str]:
    """Remove only bundle-owned PATH entries, retaining spelling/order elsewhere."""
    anchor = ntpath.normcase(ntpath.abspath(bundle))
    def bundled(value: str) -> bool:
        value = value.strip().strip('"')
        if not value:
            return False
        try:
            path = ntpath.normcase(ntpath.abspath(value))
            return ntpath.commonpath((anchor, path)) == anchor
        except ValueError:
            return False
    return {key: ';'.join(part for part in value.split(';') if not bundled(part))
            if key.upper() == 'PATH' else value for key, value in environment.items()}


def _windows_dll_directory():
    """Read the current extra directory and return a checked Win32 setter."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    get = kernel.GetDllDirectoryW
    get.argtypes = (wintypes.DWORD, wintypes.LPWSTR)
    get.restype = wintypes.DWORD
    set_directory = kernel.SetDllDirectoryW
    set_directory.argtypes = (wintypes.LPCWSTR,)
    set_directory.restype = wintypes.BOOL
    def failure(phase: str):
        return OSError(int(ctypes.get_last_error()), 'fixed diagnostic DLL environment '+phase)
    def set_checked(value: str | None, phase: str) -> None:
        ctypes.set_last_error(0)
        if not set_directory(value):
            raise failure(phase)
    ctypes.set_last_error(0)
    size = int(get(0, None))
    if size == 0:
        if ctypes.get_last_error():
            raise failure('read')
        # Empty/NULL search states cannot be distinguished by this API; leave
        # either unchanged rather than inventing a restoration state.
        return None, set_checked
    buffer = ctypes.create_unicode_buffer(size + 1)
    ctypes.set_last_error(0)
    copied = int(get(len(buffer), buffer))
    if copied == 0 or copied >= len(buffer):
        raise failure('read')
    return buffer.value, set_checked


_EXTERNAL_LAUNCH_LOCK = threading.RLock()

@contextmanager
def _external_program_environment():
    # Same checked DLL/PATH algorithm as reviewed vendor subprocess_env.
    # Only this synchronous Popen crosses the context; never await here.
    environment = {key:value for key,value in os.environ.items()
                   if not key.upper().startswith('CONTROLSTUDIO_')}
    bundle = getattr(sys, '_MEIPASS', None)
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False) or not isinstance(bundle, str) or not bundle:
        yield environment
        return
    environment = _external_windows_environment(environment, bundle)
    with _EXTERNAL_LAUNCH_LOCK:
        previous, set_directory = _windows_dll_directory()
        if previous is not None:
            set_directory(None, 'reset')
        try:
            yield environment
        finally:
            if previous is not None:
                set_directory(previous, 'restore')

def _fixed_command():
    windows=Path(os.environ.get('SystemRoot',r'C:\Windows'))
    return [str(windows/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'),
            '-NoProfile','-NonInteractive','-EncodedCommand',
            base64.b64encode(_PS_SOURCE.encode('utf-16-le')).decode('ascii')]

def _token(value):
    return value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}',value) else None

def _frame(value):
    """Accept only the fixed helper protocol; never UI/input/command payloads."""
    if not isinstance(value,dict) or value.get('schema')!=SCHEMA:raise ValueError('diagnostic schema')
    event=value.get('event')
    if event=='ready':
        if set(value)!={'schema','event','ready_utc'} or not isinstance(value['ready_utc'],str) or len(value['ready_utc'])>64:raise ValueError('diagnostic ready')
    elif event in {'baseline','start','stop'}:
        if set(value)!={'schema','event','event_time_utc','process_name','pid','ppid','exit_status','creation_time_utc','executable_path','command_line'}:raise ValueError('diagnostic fields')
        name=value['process_name']
        if not isinstance(name,str) or name.casefold() not in _NAMES:raise ValueError('diagnostic process')
        if type(value['pid']) is not int or not 1<=value['pid']<=0xffffffff or type(value['ppid']) is not int or not 0<=value['ppid']<=0xffffffff:raise ValueError('diagnostic pid')
        for key,limit,nullable in (('event_time_utc',64,False),('creation_time_utc',64,True),('executable_path',32767,True),('command_line',32767,True)):
            item=value[key]
            if item is None and nullable:continue
            if not isinstance(item,str) or len(item)>limit:raise ValueError('diagnostic scalar')
        exit_status=value['exit_status']
        if exit_status is not None and (event!='stop' or type(exit_status) is not int or not 0<=exit_status<=0xffffffff):raise ValueError('diagnostic exit status')
        if value['command_line'] is not None and (name.casefold()!='simulator.exe' or event!='start'):raise ValueError('diagnostic argument scope')
    elif event=='finished':
        if set(value)!={'schema','event','ended_utc','collection_status','record_limit_reached','query_failures','cleanup_failures','error_type','stopped_by_owner'}:raise ValueError('diagnostic finished')
        if value['collection_status'] not in {'observation_finished','collection_failed'}:raise ValueError('diagnostic status')
        for key in ('record_limit_reached','stopped_by_owner'):
            if type(value[key]) is not bool:raise ValueError('diagnostic boolean')
        for key in ('query_failures','cleanup_failures'):
            if type(value[key]) is not int or not 0<=value[key]<=100000:raise ValueError('diagnostic count')
        if not isinstance(value['ended_utc'],str) or len(value['ended_utc'])>64:raise ValueError('diagnostic time')
        if value['error_type'] is not None and (not isinstance(value['error_type'],str) or re.fullmatch(r'[A-Za-z0-9_.+]{1,128}',value['error_type']) is None):raise ValueError('diagnostic error type')
    else:raise ValueError('diagnostic event')
    return deepcopy(value)

class VendorProcessDiagnostics:
    """At most one attempt per run. Data never changes native results/admission."""
    def __init__(self,record):
        self._record=record;self._task=None;self._process=None;self._job=None;self._api=None;self._assigned=False;self._read_task=None;self._close_task=None
        self._ready=asyncio.Event();self._stop_requested=False;self._attempted=False;self._closed=False
        self._records=[];self._correlations=[];self._bytes=0;self._ready_frame=None;self._finished=None
        self._failure=None;self._partial=False;self._retired=False;self._correlation_partial=False
        self._ready_deadline=None;self._ready_late=False
    async def note_start(self,context):
        correlation={key:_token(context.get(key)) for key in ('conversation_id','run_id','request_id','call_id','workspace_id','revision_id','saved_sha256')} | {
            'target_pid':context.get('target_pid') if type(context.get('target_pid')) is int and 0<context['target_pid']<=0xffffffff else None,
            'target_process_create_time':context.get('target_process_create_time') if type(context.get('target_process_create_time')) in (int,float) and math.isfinite(context['target_process_create_time']) and context['target_process_create_time']>0 else None,
            'stage':'before_workspace_simulator_start_dispatch'}
        if len(self._correlations)<_MAX_RECORDS:self._correlations.append(correlation)
        else:self._correlation_partial=True
        try:
            if self._attempted:return
            self._attempted=True
            if os.name!='nt':
                self._failure='unsupported_platform'
                self._task=asyncio.create_task(self._publish())
                return
            self._ready_deadline=time.monotonic()+_READY_TIMEOUT_SECONDS
            self._task=asyncio.create_task(self._run(),name='controlstudio-fixed-vendor-process-diagnostic')
        finally:
            active=(self._task is not None and not self._task.done() and not self._closed
                    and self._finished is None and self._failure is None
                    and self._process is not None and self._process.poll() is None)
            correlation['observation_window_active']=active
            correlation['subscription_ready_before_dispatch']=self._ready_frame is not None and active
            correlation['subscription_late_or_unverified']=not correlation['subscription_ready_before_dispatch']
            if correlation['subscription_late_or_unverified']:self._partial=True
            # Registration has no await: optional readiness/publication/cleanup
            # must not become a precondition for the native Start dispatch.
    def _spawn(self):
        from .windows_mcp_transport import (
            _assign_process_to_cleanup_job,
            _open_process_cleanup_job,
            _resume_suspended_cleanup_process,
        )
        self._job,self._api=_open_process_cleanup_job()
        with _external_program_environment() as environment:
            # Store the exact Popen before DLL restore can fail; cleanup owns it
            # even if assignment/restore fails. This scope has no await.
            self._process=subprocess.Popen(_fixed_command(),stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=environment,
                creationflags=0x00000004|subprocess.CREATE_NO_WINDOW)
        _assign_process_to_cleanup_job(self._process,self._job,self._api)
        self._assigned=True
        _resume_suspended_cleanup_process(self._process)
    async def _readline(self):
        self._read_task=asyncio.create_task(asyncio.to_thread(self._process.stdout.readline,_MAX_LINE_BYTES+1))
        # Cancellation leaves only this one reader, joined after owned retirement.
        return await asyncio.shield(self._read_task)
    async def _run(self):
        started=time.monotonic()
        try:
            if self._stop_requested or self._closed:return
            self._spawn()
            while True:
                remaining=_DURATION_SECONDS-(time.monotonic()-started)
                if remaining<=0:self._failure='duration_limit';self._partial=True;break
                try:
                    line=await asyncio.wait_for(self._readline(),timeout=remaining)
                except asyncio.TimeoutError:
                    self._failure='duration_limit';self._partial=True;break
                if not line:break
                self._bytes+=len(line)
                if len(line)>_MAX_LINE_BYTES or self._bytes>_MAX_STDOUT_BYTES:
                    self._failure='stdout_limit';self._partial=True;break
                value=_frame(json.loads(line.decode('utf-8-sig')))
                event=value['event']
                if event=='ready':
                    if self._ready_frame is not None:raise ValueError('duplicate diagnostic ready')
                    self._ready_frame=value;self._ready.set()
                    self._ready_late=time.monotonic()>self._ready_deadline
                    self._partial=self._partial or self._ready_late
                elif event=='finished':
                    self._finished=value
                    self._partial=self._partial or value['record_limit_reached'] or value['query_failures']>0 or value['cleanup_failures']>0 or value['collection_status']!='observation_finished'
                    break
                else:
                    if len(self._records)>=_MAX_RECORDS:self._partial=True;break
                    self._records.append(value)
        except asyncio.CancelledError:
            self._partial=True
            raise
        except Exception:
            self._failure='protocol_or_process_failure';self._partial=True
        finally:
            if self._ready_frame is None:self._partial=True
            self._ready.set()
            try:
                await native_checkpoint_boundary(self._cleanup())
            finally:
                await native_checkpoint_boundary(self._publish())
    async def _cleanup(self):
        process=self._process
        try:
            if process is not None and process.poll() is None:
                if not self._assigned:process.kill()
                if process.stdin is not None:
                    try:process.stdin.write(b'stop\n');process.stdin.flush();process.stdin.close()
                    except Exception:pass
                try:await asyncio.to_thread(process.wait,timeout=_DRAIN_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:pass
            if self._job is not None:
                from .windows_mcp_transport import _retire_process_cleanup_job
                await _retire_process_cleanup_job(self._job,self._api,timeout=_CLEANUP_TIMEOUT_SECONDS)
                self._job=None
            if process is not None:await asyncio.to_thread(process.wait,timeout=_CLEANUP_TIMEOUT_SECONDS)
            if self._read_task is not None:
                await asyncio.wait_for(asyncio.shield(self._read_task),timeout=_CLEANUP_TIMEOUT_SECONDS)
            self._retired=True
        except Exception:
            self._failure=self._failure or 'cleanup_unverified';self._partial=True
        finally:
            if self._job is not None:
                try:self._api[0].CloseHandle(self._job)
                except Exception:pass
                self._job=None
            # No close/read lock race: retire the tree and join the sole pipe
            # reader before closing its file. An unverified join stays partial.
            if process is not None and (self._read_task is None or self._read_task.done()):
                for pipe in (process.stdin,process.stdout):
                    if pipe is not None:
                        try:pipe.close()
                        except Exception:pass
    def snapshot(self):
        result={'schema':SCHEMA,'evidence_scope':'fixed_vendor_process_lifecycle_only',
            'collection_saved_utc':datetime.now(timezone.utc).isoformat(),'ready':deepcopy(self._ready_frame),
            'subscription_ready_after_startup_window':self._ready_late,
            'finished':deepcopy(self._finished),'records':deepcopy(self._records),
            'start_call_correlations':deepcopy(self._correlations),'correlations_partial':self._correlation_partial,
            'diagnostic_partial':self._partial or self._finished is None,'diagnostic_failure':self._failure,
            'owned_tree_retired_verified':self._retired,'delivery_completeness_verified':False,
            'causal_parentage_verified':False,'native_input_attempted':False}
        # Storage bytes stay bounded even when a JSON writer escapes Unicode.
        while len(json.dumps(result,ensure_ascii=True,separators=(',',':')).encode('utf8'))>_MAX_REPORT_BYTES:
            result['diagnostic_partial']=True
            if result['records']:result['records'].pop()
            elif result['start_call_correlations']:
                result['start_call_correlations'].pop();result['correlations_partial']=True
            else:raise ValueError('fixed diagnostic metadata limit')
        return result
    async def _publish(self):
        try:await asyncio.to_thread(self._record,self.snapshot())
        except Exception:_LOG.warning('Fixed vendor lifecycle diagnostic could not be retained')
    async def close(self):
        # Every caller joins the same close boundary, even after cancellation.
        if self._close_task is None:
            self._close_task=asyncio.create_task(self._close_owned())
        await native_checkpoint_boundary(self._close_task)
    async def _close_owned(self):
        if self._closed:return
        self._closed=True
        task=self._task
        if task is None:return
        if not task.done():
            self._stop_requested=True
            if self._process is not None and self._process.stdin is not None:
                try:self._process.stdin.write(b'stop\n');self._process.stdin.flush()
                except Exception:pass
            try:await asyncio.wait_for(asyncio.shield(task),timeout=_DRAIN_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                task.cancel()
                await native_checkpoint_boundary(asyncio.gather(task,return_exceptions=True))
        else:
            await asyncio.gather(task,return_exceptions=True)
        # Later Start correlations do not spawn a second observer or lose provenance.
        await self._publish()
