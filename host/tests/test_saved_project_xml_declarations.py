import gzip

import pytest

from controlstudio_host.native_workspace_host import saved_project_observation


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-le", "utf-16-be"])
@pytest.mark.parametrize("declared", [False, True])
def test_saved_project_catalog_rejects_encoded_declarations(tmp_path, encoding, declared):
    declaration = '<!DOCTYPE Project [<!ENTITY probe "must-not-expand">]>' if declared else ''
    xml = (declaration + '<Project><Configuration><Program>Main<Body>'
           '<LDRoutine/></Body></Program></Configuration></Project>').encode(encoding)
    path = tmp_path / "synthetic.xgwx"
    path.write_bytes(b"synthetic-header" + gzip.compress(xml))
    observed = saved_project_observation(path)
    assert observed['stable_during_read'] is True
    if declared:
        assert observed['program_names_error'] == {
            'kind': 'saved_program_catalog_unavailable', 'exception_kind': 'ValueError'}
        assert observed['saved_sections']['status'] == 'unavailable'
        assert 'program_names' not in observed
    else:
        assert observed['program_names'] == ['Main']
        assert observed['program_names_source'] == 'saved_project_xml_same_hashed_bytes'
