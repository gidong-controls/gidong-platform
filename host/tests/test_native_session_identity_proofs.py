from controlstudio_host.native_session_identity import (
    _selected_pid_identity_proof_is_strong,
    native_trial_resume_matches_expected,
)


def _common_proof() -> dict[str, object]:
    return {
        "method": "",
        "same_active_object": False,
        "stable_repeat": True,
        "pid": 11,
        "main_hwnd": 22,
        "project_path": r"C:\work\project.xgwx",
        "project_hash": "a" * 64,
        "process_create_time": 1.0,
        "version": None,
    }


def test_rot_and_native_file_selected_process_proofs_are_supported() -> None:
    native_file = _common_proof()
    native_file["method"] = "selected_pid_exact_window_title_native_file_use_v1"
    assert _selected_pid_identity_proof_is_strong(native_file)

    rot = _common_proof()
    rot.update(
        {
            "method": "same_rot_object_project_tree_and_path_v1",
            "same_active_object": True,
            "project_tree_hwnd": 33,
        }
    )
    assert _selected_pid_identity_proof_is_strong(rot)


def test_selected_process_proof_shape_and_method_semantics_fail_closed() -> None:
    mismatched = _common_proof()
    mismatched.update(
        {
            "method": "same_rot_object_project_tree_and_path_v1",
            "same_active_object": False,
            "project_tree_hwnd": 33,
        }
    )
    assert not _selected_pid_identity_proof_is_strong(mismatched)

    unexpected = _common_proof()
    unexpected.update(
        {
            "method": "selected_pid_exact_window_title_native_file_use_v1",
            "unexpected": True,
        }
    )
    assert not _selected_pid_identity_proof_is_strong(unexpected)


def test_native_trial_resume_tolerates_only_sub_millisecond_time_precision() -> None:
    expected = {"pid": 11, "process_create_time": 1000.1234567}
    observed = {"pid": 11, "process_create_time": 1000.1234}
    assert native_trial_resume_matches_expected(observed, expected)

    changed_lifetime = {"pid": 11, "process_create_time": 1000.125}
    assert not native_trial_resume_matches_expected(changed_lifetime, expected)

    changed_pid = {"pid": 12, "process_create_time": 1000.1234}
    assert not native_trial_resume_matches_expected(changed_pid, expected)
