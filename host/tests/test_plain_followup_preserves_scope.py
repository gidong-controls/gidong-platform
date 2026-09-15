from controlstudio_host.project_planning import append_planning_clarification


def test_plain_followups_keep_established_scope_without_repeating_it():
    brief = {
        "project_name": "대동인덱스",
        "machine_purpose": "인덱스 동작을 만들어 시험한다.",
        "hardware_and_io": "XGK-CPUE, XG-SIM 내부 신호로 별도 작업 사본에서 시험한다.",
        "safety_and_interlocks": "기존 파일과 실제 장비에는 쓰지 않는다.",
        "operating_modes": "자동 반복, 수동 한 번에 한 칸.",
    }
    original = dict(brief)
    for message in ("계속해", "수동 버튼을 누르고 있어도 한 번만 움직이게 해줘", "이제 시험해줘"):
        brief = append_planning_clarification(brief, message)
        for key, value in original.items():
            assert brief[key] == value
    assert brief["supplemental_requirements"].count("계속해") == 1
