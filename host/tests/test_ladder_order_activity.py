from controlstudio_host.app import _agent_tool_activity_projection


def test_ladder_order_edit_has_human_readable_activity():
    result = _agent_tool_activity_projection(
        tool_name="design.move_ladder_rung",
        event_type="tool.completed",
        change_request_scope="analysis_only",
        change_outcome_state=None,
        simulator_target_status=None,
    )
    assert result["activity_label"] == "프로그램 실행 순서 수정"
    assert result["activity_result"] == "설계 초안의 래더 실행 순서를 변경했습니다."
