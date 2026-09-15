$ErrorActionPreference = 'Stop'
$platformRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $platformRoot
Push-Location -LiteralPath $workspaceRoot
try {
    # In-memory fixture only: no model call, project file, vendor UI, or PLC access.
    $incrementFixture = & .\gidong-agent\.venv\Scripts\python.exe -c "import sys,json;sys.path.insert(0,'gidong-agent/tests');from test_native_output_correction import NativeOutputCorrectionTests;c=NativeOutputCorrectionTests();c.test_increment_survives_provider_to_domain_without_becoming_a_level_count();print(json.dumps(c.increment_design))"
    if ($LASTEXITCODE -ne 0) { throw 'Agent increment fixture failed' }
    $hostIncrement = $incrementFixture | & .\gidong-platform\host\.venv\Scripts\python.exe -c "import sys,json;from controlstudio_host.api_models import ProjectPlanNativeDesignV1;d=ProjectPlanNativeDesignV1.model_validate(json.load(sys.stdin));print(d.model_dump_json(by_alias=True))"
    if ($LASTEXITCODE -ne 0) { throw 'Host increment contract failed' }
    $compilerCheck = @'
import sys,json,copy
from xg5000_control.project.native_create import _compile_reviewed_native_program
from xg5000_control.errors import XG5000Error
d=json.load(sys.stdin)
def compile_design(design):
    return _compile_reviewed_native_program({'technicalConfiguration':{'cpuModel':'XGK-CPUE'},'design':design},program_name='MAIN',plc_series='XGK')
r=compile_design(d)
assert 'INCP D00100' in r['source']
assert 'MOV 0 D00100' in r['source']
assert len(r['rungIds'])==4
for defect in ('missing_reset','nonzero_reset','overlap','or_bypass','physical_address'):
    bad=copy.deepcopy(d)
    rungs=bad['ladderRungs']
    if defect=='missing_reset': rungs.pop()
    elif defect=='nonzero_reset': rungs[-1]['action']['sourceConstant']=1
    elif defect=='overlap': rungs[-1]['condition']['state']='on'
    elif defect=='physical_address': rungs[-2]['action']['destinationAddress']='P00100'
    else:
        rungs[-2]['condition']={'kind':'any_of','terms':[rungs[-2]['condition'],rungs[-1]['condition']]}
    try: compile_design(bad)
    except XG5000Error: pass
    else: raise AssertionError('MCP accepted '+defect)
print('PASS: Agent -> Host -> MCP increment/reset compilation and independent invalid-design rejection; no native execution')
'@
    $hostIncrement | & .\gidong-plc-mcp\.venv\Scripts\python.exe -c $compilerCheck
    if ($LASTEXITCODE -ne 0) { throw 'MCP increment compilation failed' }
} finally {
    Pop-Location
}
