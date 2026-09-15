$ErrorActionPreference = 'Stop'
$platformRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $platformRoot
Push-Location -LiteralPath $workspaceRoot
try {
    # Full indexer proposal, in memory only. Not a user-approved plan or native run.
    $referenceDesign = & .\gidong-agent\.venv\Scripts\python.exe -c "import sys,json;sys.path.insert(0,'gidong-agent/tests');from daedong_index_reference import artifact_fixture;print(json.dumps(artifact_fixture()['design']))"
    if ($LASTEXITCODE -ne 0) { throw 'Full reference Agent contract failed' }
    $hostDesign = $referenceDesign | & .\gidong-platform\host\.venv\Scripts\python.exe -c "import sys,json;from controlstudio_host.api_models import ProjectPlanNativeDesignV1;d=ProjectPlanNativeDesignV1.model_validate(json.load(sys.stdin));print(d.model_dump_json(by_alias=True))"
    if ($LASTEXITCODE -ne 0) { throw 'Full reference Host contract failed' }
    $compilerCheck = @'
import sys,json
from xg5000_control.project.native_create import _compile_reviewed_native_program
from xg5000_control.live.native_simulation import NativeSimulationRunner
d=json.load(sys.stdin)
plan={'technicalConfiguration':{'cpuModel':'XGK-CPUE'},'design':d}
r=_compile_reviewed_native_program(plan,program_name='MAIN',plc_series='XGK')
assert len(d['operatingModes'])==2 and len(d['sequences'])==2 and len(d['alarms'])==3
assert {s['restartPolicy'] for s in d['sequences']}=={'continuous','require_start_release'}
assert 'INCP D00100' in r['source'] and 'MOV 0 D00100' in r['source']
assert d['timers'][0]['timeBaseMs']*d['timers'][0]['presetTicks']==5000
assert len(r['rungIds'])==23
# Explicitly expose the still-missing full simulation profile; do not report
# simple counter observations as automatic/manual acceptance.
try: NativeSimulationRunner(object(),plan,r,'XGK')
except ValueError as exc:
    assert 'at most one confirmed operatingModes' in str(exc),str(exc)
else: raise AssertionError('Update this test when full mode simulation is implemented')
print('PASS: full 2-mode / 2-sequence / 3-alarm / 5-second / count+reset proposal compiles through Agent -> Host -> MCP (23 rungs)')
print('NOT VERIFIED: native creation, save/reopen and full XG-SIM acceptance; multi-mode runner support is still missing')
'@
    $hostDesign | & .\gidong-plc-mcp\.venv\Scripts\python.exe -c $compilerCheck
    if ($LASTEXITCODE -ne 0) { throw 'Full reference MCP compilation failed' }
} finally {
    Pop-Location
}
