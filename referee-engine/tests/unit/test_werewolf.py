import asyncio
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from werewolf import (  # noqa: E402
    DEFAULT_ROLE_COUNTS,
    PERSONALITY_POOL,
    ROLE_GUARD,
    ROLE_HUNTER,
    ROLE_KNIGHT,
    ROLE_SEER,
    ROLE_VILLAGER,
    ROLE_WEREWOLF,
    ROLE_WHITE_WOLF_KING,
    ROLE_WITCH,
    TEAM_GOOD,
    TEAM_WEREWOLF,
    WHITE_WOLF_KING_KNIGHT_ROLE_COUNTS,
    WEREWOLF_BOARD_WHITE_WOLF_KING_KNIGHT,
    WerewolfJudgeConfig,
    WerewolfMatchRunner,
    AgentAction,
    apply_judgement_to_state,
    create_werewolf_state,
    judge_werewolf_match,
    render_action_prompt,
    render_werewolf_training_prompt,
)


def test_create_werewolf_state_requires_12_players_and_roles():
    state = create_werewolf_state(list(range(1, 13)), seed=1)
    assert state.role_counts() == DEFAULT_ROLE_COUNTS
    assert len(state.players) == 12
    assert {player.personality for player in state.players.values()}
    assert len({player.personality for player in state.players.values()}) == len(PERSONALITY_POOL)

    with pytest.raises(ValueError):
        create_werewolf_state(list(range(1, 11)), seed=1)


def test_werewolf_personality_enters_prompts_without_revealing_other_roles():
    state = create_werewolf_state(list(range(1, 13)), seed=7)
    player = state.players[1]

    training = render_werewolf_training_prompt(1, player.personality, player.style_hint)
    assert player.personality in training
    assert player.style_hint in training

    prompt = render_action_prompt(state, 1, request="测试行动", allowed_actions=["pass"])
    assert player.personality in prompt
    other_role = state.players[2].role
    assert f'"2":' not in prompt or other_role not in prompt


def test_create_white_wolf_king_knight_board_roles():
    state = create_werewolf_state(
        list(range(1, 13)),
        board=WEREWOLF_BOARD_WHITE_WOLF_KING_KNIGHT,
        role_counts=WHITE_WOLF_KING_KNIGHT_ROLE_COUNTS,
        seed=101,
    )

    assert state.board == WEREWOLF_BOARD_WHITE_WOLF_KING_KNIGHT
    assert state.role_counts() == WHITE_WOLF_KING_KNIGHT_ROLE_COUNTS
    assert state.role_counts()[ROLE_GUARD] == 0
    assert state.role_counts()[ROLE_WHITE_WOLF_KING] == 1
    assert state.role_counts()[ROLE_KNIGHT] == 1
    assert len(state.alive_wolf_ids) == 4


def test_main_exports_werewolf_message_mode_and_findmini_defaults():
    import importlib

    main = importlib.import_module("main")

    assert main.MESSAGE_MODE_NORMAL == "normal"
    llm = main.LLMConfig()
    assert llm.baseUrl == "https://api.findmini.top/gpt"
    assert llm.model == "gpt-5.5"
    assert llm.proxy == ""


def test_night_resolution_handles_guard_witch_double_save_and_poison():
    state = create_werewolf_state(list(range(1, 13)), seed=2)
    victim = next(pid for pid, player in state.players.items() if player.role != ROLE_WEREWOLF)
    poison = next(pid for pid, player in state.players.items() if player.role == ROLE_WEREWOLF)

    state.day = 1
    resolution = state.resolve_night(
        wolf_target=victim,
        guard_target=victim,
        witch_save=True,
        witch_poison_target=poison,
        seer_target=None,
    )

    assert victim in resolution.deaths
    assert resolution.reasons[victim] == "guard_witch_double_save"
    assert poison in resolution.deaths
    assert resolution.reasons[poison] == "witch_poison"
    assert state.witch_has_save is False
    assert state.witch_has_poison is False


def test_guard_pass_night_clears_consecutive_guard_restriction():
    # Rule: 不能连续两晚守同一人 — the restriction only applies to the IMMEDIATELY
    # preceding night. A pass (no guard) night must clear last_guard_target so the guard
    # may re-protect the earlier target the following night.
    state = create_werewolf_state(list(range(1, 13)), seed=5)
    protected = next(pid for pid, player in state.players.items() if player.role != ROLE_WEREWOLF)

    # Night 1: guard protects `protected`.
    state.day = 1
    state.resolve_night(
        wolf_target=None,
        guard_target=protected,
        witch_save=False,
        witch_poison_target=None,
        seer_target=None,
    )
    assert state.last_guard_target == protected

    # Night 2: guard passes (no valid target). The restriction must reset to None so that
    # `protected` is guardable again on night 3 (nights 1 and 3 are not consecutive).
    state.day = 2
    state.resolve_night(
        wolf_target=None,
        guard_target=None,
        witch_save=False,
        witch_poison_target=None,
        seer_target=None,
    )
    assert state.last_guard_target is None


def test_night_resolution_can_defer_first_night_deaths_until_after_sheriff():
    state = create_werewolf_state(list(range(1, 13)), seed=12)
    victim = next(pid for pid, player in state.players.items() if player.role != ROLE_WEREWOLF)

    state.day = 1
    resolution = state.resolve_night(
        wolf_target=victim,
        guard_target=None,
        witch_save=False,
        witch_poison_target=None,
        seer_target=None,
        apply_deaths=False,
    )

    assert victim in resolution.deaths
    assert state.players[victim].alive is True
    assert state.last_night_deaths == []

    applied = state.apply_night_resolution(resolution)
    assert applied == [victim]
    assert state.players[victim].alive is False
    assert state.last_night_deaths == [victim]


def test_white_wolf_king_is_wolf_for_seer_and_win_conditions():
    state = create_werewolf_state(
        list(range(1, 13)),
        board=WEREWOLF_BOARD_WHITE_WOLF_KING_KNIGHT,
        role_counts=WHITE_WOLF_KING_KNIGHT_ROLE_COUNTS,
        seed=102,
    )
    white_wolf = next(pid for pid, player in state.players.items() if player.role == ROLE_WHITE_WOLF_KING)
    seer = next(pid for pid, player in state.players.items() if player.role == ROLE_SEER)
    villagers = [pid for pid, player in state.players.items() if player.role == ROLE_VILLAGER]
    gods = [pid for pid, player in state.players.items() if player.role in {ROLE_SEER, ROLE_WITCH, ROLE_HUNTER, ROLE_KNIGHT}]

    state.resolve_night(
        wolf_target=None,
        guard_target=None,
        witch_save=False,
        witch_poison_target=None,
        seer_target=white_wolf,
    )

    assert white_wolf in state.wolf_ids
    assert state.seer_checks[white_wolf] == "wolf"
    assert any(
        event["type"] == "SEER_CHECK_RESULT" and event["data"]["target_player_id"] == white_wolf
        for event in state.private_events[seer]
    )

    for pid in villagers:
        state.kill_player(pid, "test")

    assert state.check_win() == (TEAM_WEREWOLF, "all_villagers_eliminated")

    state = create_werewolf_state(
        list(range(1, 13)),
        board=WEREWOLF_BOARD_WHITE_WOLF_KING_KNIGHT,
        role_counts=WHITE_WOLF_KING_KNIGHT_ROLE_COUNTS,
        seed=103,
    )
    for pid in [pid for pid, player in state.players.items() if player.role in {ROLE_SEER, ROLE_WITCH, ROLE_HUNTER, ROLE_KNIGHT}]:
        state.kill_player(pid, "test")
    assert state.check_win() == (TEAM_WEREWOLF, "all_gods_eliminated")


def test_werewolf_win_condition_is_side_slaughter_not_parity():
    state = create_werewolf_state(list(range(1, 13)), seed=13)
    wolves = [pid for pid, player in state.players.items() if player.role == ROLE_WEREWOLF]
    gods = [pid for pid, player in state.players.items() if player.role in {"seer", "witch", "hunter", "guard"}]
    villagers = [pid for pid, player in state.players.items() if player.role == "villager"]

    for pid in villagers[:2]:
        state.kill_player(pid, "test")
    for pid in gods[:2]:
        state.kill_player(pid, "test")

    assert len(state.alive_wolf_ids) == len([pid for pid in state.alive_ids if pid not in wolves])
    assert state.check_win() == (None, None)

    for pid in villagers[2:]:
        state.kill_player(pid, "test")

    assert state.check_win() == (TEAM_WEREWOLF, "all_villagers_eliminated")


def test_werewolf_reveal_only_allows_alive_wolf_in_public_speech_phase():
    state = create_werewolf_state(list(range(1, 13)), seed=3)
    wolf = next(pid for pid, player in state.players.items() if player.role == ROLE_WEREWOLF)
    non_wolf = next(pid for pid, player in state.players.items() if player.role != ROLE_WEREWOLF)

    ok, reason = state.reveal_self(non_wolf, "day_speech")
    assert ok is False
    assert reason == "not_werewolf"

    ok, reason = state.reveal_self(wolf, "night")
    assert ok is False
    assert reason == "illegal_phase"

    ok, reason = state.reveal_self(wolf, "day_speech")
    assert ok is True
    assert reason == "revealed"
    assert state.players[wolf].alive is False
    assert state.players[wolf].revealed is True


@pytest.mark.asyncio
async def test_white_wolf_king_reveal_kills_self_and_target_and_allows_hunter_shot():
    state = create_werewolf_state(
        list(range(1, 13)),
        board=WEREWOLF_BOARD_WHITE_WOLF_KING_KNIGHT,
        role_counts=WHITE_WOLF_KING_KNIGHT_ROLE_COUNTS,
        seed=104,
    )
    state.day = 1
    white_wolf = next(pid for pid, player in state.players.items() if player.role == ROLE_WHITE_WOLF_KING)
    hunter = next(pid for pid, player in state.players.items() if player.role == ROLE_HUNTER)
    hunter_target = next(pid for pid in state.alive_ids if pid not in {white_wolf, hunter})
    events = []

    async def agent_request(pid, _prompt, kind, _timeout):
        if kind == "werewolf_hunter_shoot" and pid == hunter:
            return f'{{"action":"hunter_shoot","target_player_id":{hunter_target},"reason":"revenge"}}'
        return '{"action":"pass"}'

    async def emit_event(event_type, data, *, audience='public'):
        events.append({"type": event_type, "data": data, "audience": audience})

    runner = WerewolfMatchRunner(
        state,
        agent_request=agent_request,
        emit_event=emit_event,
        set_status=lambda *_args: asyncio.sleep(0),
    )

    handled = await runner._handle_white_wolf_king_reveal(
        white_wolf,
        AgentAction(action="white_wolf_king_reveal", target_player_id=hunter, reason="take hunter"),
        "day_speech",
    )

    assert handled is True
    assert state.players[white_wolf].alive is False
    assert state.players[hunter].alive is False
    assert state.players[hunter].death_reason == "white_wolf_king_takeaway"
    assert state.players[hunter_target].alive is False
    assert state.players[hunter_target].death_reason == "hunter_shot"
    assert any(event["type"] == "WEREWOLF_WHITE_WOLF_KING_REVEALED" for event in events)
    assert any(event["type"] == "WEREWOLF_HUNTER_SHOT" for event in events)


@pytest.mark.asyncio
async def test_white_wolf_king_invalid_reveal_does_not_kill():
    state = create_werewolf_state(
        list(range(1, 13)),
        board=WEREWOLF_BOARD_WHITE_WOLF_KING_KNIGHT,
        role_counts=WHITE_WOLF_KING_KNIGHT_ROLE_COUNTS,
        seed=105,
    )
    white_wolf = next(pid for pid, player in state.players.items() if player.role == ROLE_WHITE_WOLF_KING)
    target = next(pid for pid in state.alive_ids if pid != white_wolf)
    events = []

    async def emit_event(event_type, data, *, audience='public'):
        events.append({"type": event_type, "data": data, "audience": audience})

    runner = WerewolfMatchRunner(
        state,
        agent_request=lambda *_args: asyncio.sleep(0),
        emit_event=emit_event,
        set_status=lambda *_args: asyncio.sleep(0),
    )

    handled = await runner._handle_white_wolf_king_reveal(
        white_wolf,
        AgentAction(action="white_wolf_king_reveal", target_player_id=target),
        "night",
    )

    assert handled is False
    assert state.players[white_wolf].alive is True
    assert state.players[target].alive is True
    assert events[-1]["data"]["action"] == "invalid_white_wolf_king_reveal"


@pytest.mark.asyncio
async def test_sheriff_vote_uses_one_point_five_weight_for_sheriff():
    state = create_werewolf_state(list(range(1, 13)), seed=4)
    runner = WerewolfMatchRunner(
        state,
        agent_request=lambda *_args: asyncio.sleep(0),
        emit_event=lambda *_args: asyncio.sleep(0),
        set_status=lambda *_args: asyncio.sleep(0),
    )
    state.sheriff_id = 1
    winner, tied = runner._tally_votes([(1, 2, 1.5), (3, 4, 1.0), (5, 4, 1.0)])
    assert winner == 4
    assert tied == [4]

    winner, tied = runner._tally_votes([(1, 2, 1.5), (3, 4, 1.0), (5, 4, 0.5)])
    assert winner is None
    assert tied == [2, 4]


@pytest.mark.asyncio
async def test_knight_duel_hit_wolf_ends_day_and_cannot_trigger_white_wolf_takeaway():
    state = create_werewolf_state(
        list(range(1, 13)),
        board=WEREWOLF_BOARD_WHITE_WOLF_KING_KNIGHT,
        role_counts=WHITE_WOLF_KING_KNIGHT_ROLE_COUNTS,
        seed=106,
    )
    state.day = 1
    knight = next(pid for pid, player in state.players.items() if player.role == ROLE_KNIGHT)
    white_wolf = next(pid for pid, player in state.players.items() if player.role == ROLE_WHITE_WOLF_KING)
    events = []

    async def emit_event(event_type, data, *, audience='public'):
        events.append({"type": event_type, "data": data, "audience": audience})

    runner = WerewolfMatchRunner(
        state,
        agent_request=lambda *_args: asyncio.sleep(0),
        emit_event=emit_event,
        set_status=lambda *_args: asyncio.sleep(0),
    )

    hit = await runner._maybe_handle_knight_duel(
        knight,
        AgentAction(action="knight_duel", target_player_id=white_wolf, reason="lock wolf"),
        "day_speech",
    )

    assert hit is True
    assert state.knight_duel_used is True
    assert state.players[white_wolf].alive is False
    assert state.players[white_wolf].death_reason == "knight_duel_hit_wolf"
    assert state.players[knight].alive is True
    event = next(event for event in events if event["type"] == "WEREWOLF_KNIGHT_DUEL")
    assert event["data"]["hit_wolf"] is True
    assert event["data"]["dead_player_id"] == white_wolf


@pytest.mark.asyncio
async def test_knight_duel_miss_kills_knight_and_can_only_be_used_once():
    state = create_werewolf_state(
        list(range(1, 13)),
        board=WEREWOLF_BOARD_WHITE_WOLF_KING_KNIGHT,
        role_counts=WHITE_WOLF_KING_KNIGHT_ROLE_COUNTS,
        seed=107,
    )
    state.day = 1
    knight = next(pid for pid, player in state.players.items() if player.role == ROLE_KNIGHT)
    villager = next(pid for pid, player in state.players.items() if player.role == ROLE_VILLAGER)
    wolf = next(pid for pid, player in state.players.items() if player.role == ROLE_WEREWOLF)
    events = []

    async def emit_event(event_type, data, *, audience='public'):
        events.append({"type": event_type, "data": data, "audience": audience})

    runner = WerewolfMatchRunner(
        state,
        agent_request=lambda *_args: asyncio.sleep(0),
        emit_event=emit_event,
        set_status=lambda *_args: asyncio.sleep(0),
    )

    hit = await runner._maybe_handle_knight_duel(
        knight,
        AgentAction(action="knight_duel", target_player_id=villager),
        "day_speech",
    )
    second = await runner._maybe_handle_knight_duel(
        knight,
        AgentAction(action="knight_duel", target_player_id=wolf),
        "day_speech",
    )

    assert hit is False
    assert state.players[knight].alive is False
    assert state.players[knight].death_reason == "knight_duel_missed"
    assert state.players[villager].alive is True
    assert second is False
    assert state.players[wolf].alive is True
    assert events[-1]["data"]["valid"] is False


@pytest.mark.asyncio
async def test_knight_duel_illegal_in_sheriff_and_night_phases():
    state = create_werewolf_state(
        list(range(1, 13)),
        board=WEREWOLF_BOARD_WHITE_WOLF_KING_KNIGHT,
        role_counts=WHITE_WOLF_KING_KNIGHT_ROLE_COUNTS,
        seed=108,
    )
    knight = next(pid for pid, player in state.players.items() if player.role == ROLE_KNIGHT)
    wolf = next(pid for pid, player in state.players.items() if player.role == ROLE_WEREWOLF)
    events = []

    async def emit_event(event_type, data, *, audience='public'):
        events.append({"type": event_type, "data": data, "audience": audience})

    runner = WerewolfMatchRunner(
        state,
        agent_request=lambda *_args: asyncio.sleep(0),
        emit_event=emit_event,
        set_status=lambda *_args: asyncio.sleep(0),
    )

    assert await runner._maybe_handle_knight_duel(knight, AgentAction(action="knight_duel", target_player_id=wolf), "sheriff_speech") is False
    assert await runner._maybe_handle_knight_duel(knight, AgentAction(action="knight_duel", target_player_id=wolf), "night") is False
    assert state.players[wolf].alive is True
    assert all(event["data"]["valid"] is False for event in events if event["type"] == "WEREWOLF_KNIGHT_DUEL")


@pytest.mark.asyncio
async def test_sheriff_election_fallback_creates_candidates_when_all_pass():
    state = create_werewolf_state(list(range(1, 13)), seed=8)
    events = []

    async def agent_request(_pid, _prompt, _kind, _timeout):
        return '{"action":"pass","reason":"test"}'

    async def emit_event(event_type, data, *, audience='public'):
        events.append({"type": event_type, "data": data, "audience": audience})

    runner = WerewolfMatchRunner(
        state,
        agent_request=agent_request,
        emit_event=emit_event,
        set_status=lambda *_args: asyncio.sleep(0),
    )

    await runner._run_sheriff_election()

    candidates = [
        event for event in events
        if event["type"] == "WEREWOLF_SHERIFF_CANDIDATE_DECLARED"
    ]
    assert candidates
    assert any(event["data"].get("reason") == "fallback_no_candidate" for event in candidates)


@pytest.mark.asyncio
async def test_sheriff_vote_only_allows_off_sheriff_voters():
    state = create_werewolf_state(list(range(1, 13)), seed=14)
    events = []
    asked = []
    candidates = [1, 2]
    for pid in candidates:
        state.players[pid].sheriff_candidate = True
    state.players[3].sheriff_candidate = True
    state.players[3].sheriff_withdrawn = True

    async def agent_request(pid, _prompt, kind, _timeout):
        asked.append((pid, kind))
        return '{"action":"sheriff_vote","target_player_id":1,"reason":"off sheriff vote"}'

    async def emit_event(event_type, data, *, audience='public'):
        events.append({"type": event_type, "data": data, "audience": audience})

    runner = WerewolfMatchRunner(
        state,
        agent_request=agent_request,
        emit_event=emit_event,
        set_status=lambda *_args: asyncio.sleep(0),
    )

    winner, tied = await runner._run_sheriff_vote(candidates, stage="sheriff_vote")

    asked_ids = [pid for pid, _kind in asked]
    assert 1 not in asked_ids
    assert 2 not in asked_ids
    assert 3 not in asked_ids
    assert set(asked_ids) == set(range(4, 13))
    assert winner == 1
    assert tied == [1]
    batch = next(event for event in events if event["type"] == "WEREWOLF_SHERIFF_VOTE_BATCH")
    assert batch["data"]["eligible_voters"] == list(range(4, 13))


@pytest.mark.asyncio
async def test_first_night_deaths_are_published_after_sheriff_election():
    state = create_werewolf_state(list(range(1, 13)), seed=15)
    wolves = state.alive_wolf_ids
    victim = next(pid for pid in state.alive_ids if state.players[pid].role != ROLE_WEREWOLF)
    events = []

    async def agent_request(pid, _prompt, kind, _timeout):
        if kind == "werewolf_night_kill" and pid in wolves:
            return f'{{"action":"night_kill","target_player_id":{victim},"reason":"first night"}}'
        if kind == "werewolf_run_for_sheriff" and pid == victim:
            return '{"action":"run_for_sheriff","reason":"can still run before death is announced"}'
        if kind == "werewolf_sheriff_speech":
            return '{"action":"speak","text":"警上发言","reason":"campaign"}'
        if kind == "werewolf_sheriff_vote":
            return f'{{"action":"sheriff_vote","target_player_id":{victim},"reason":"vote"}}'
        return '{"action":"pass"}'

    async def emit_event(event_type, data, *, audience='public'):
        events.append({"type": event_type, "data": data, "audience": audience})

    runner = WerewolfMatchRunner(
        state,
        agent_request=agent_request,
        emit_event=emit_event,
        set_status=lambda *_args: asyncio.sleep(0),
    )

    resolution = await runner._run_night(defer_death_publication=True)
    assert victim in resolution.deaths
    assert state.players[victim].alive is True

    await runner._run_sheriff_election()
    candidate_events = [event for event in events if event["type"] == "WEREWOLF_SHERIFF_CANDIDATE_DECLARED"]
    assert any(event["data"]["player_id"] == victim for event in candidate_events)
    assert state.players[victim].alive is True

    await runner._publish_deferred_night_resolution(resolution)
    assert state.players[victim].alive is False
    night_publications = [
        event for event in events
        if event["type"] == "WEREWOLF_NIGHT_ACTION" and event["data"].get("action") == "night_resolved"
    ]
    assert night_publications[-1]["data"]["dead_players"] == [victim]


@pytest.mark.asyncio
async def test_exile_vote_fallback_emits_visible_votes_when_all_pass():
    state = create_werewolf_state(list(range(1, 13)), seed=9)
    state.day = 1
    events = []

    async def agent_request(_pid, _prompt, _kind, _timeout):
        return '{"action":"pass","reason":"test"}'

    async def emit_event(event_type, data, *, audience='public'):
        events.append({"type": event_type, "data": data, "audience": audience})

    runner = WerewolfMatchRunner(
        state,
        agent_request=agent_request,
        emit_event=emit_event,
        set_status=lambda *_args: asyncio.sleep(0),
    )

    winner, tied = await runner._collect_exile_votes(state.alive_ids, stage="day_vote")

    assert winner is not None or tied
    votes = [event for event in events if event["type"] == "WEREWOLF_VOTE_CAST"]
    assert votes
    assert any(event["data"].get("reason") == "fallback_no_valid_exile_votes" for event in votes)


@pytest.mark.asyncio
async def test_wolf_night_public_events_are_emitted():
    state = create_werewolf_state(list(range(1, 13)), seed=10)
    state.day = 1
    events = []
    wolves = state.alive_wolf_ids
    victim = next(pid for pid in state.alive_ids if state.players[pid].role != ROLE_WEREWOLF)

    async def agent_request(pid, _prompt, kind, _timeout):
        if kind == "werewolf_wolf_chat" and pid in wolves:
            return '{"action":"speak","text":"今晚集中刀关键神职","reason":"pressure"}'
        if kind == "werewolf_night_kill" and pid in wolves:
            return f'{{"action":"night_kill","target_player_id":{victim},"reason":"统一刀口"}}'
        return '{"action":"pass"}'

    async def emit_event(event_type, data, *, audience='public'):
        events.append({"type": event_type, "data": data, "audience": audience})

    runner = WerewolfMatchRunner(
        state,
        agent_request=agent_request,
        emit_event=emit_event,
        set_status=lambda *_args: asyncio.sleep(0),
    )

    target = await runner._wolf_actions()

    assert target == victim
    spectator_events = [
        event for event in events
        if event["type"] in {
            "WEREWOLF_WOLF_CHAT_PUBLIC",
            "WEREWOLF_WOLF_KILL_VOTE_CAST",
            "WEREWOLF_WOLF_KILL_DECIDED",
        }
    ]
    assert any(event["type"] == "WEREWOLF_WOLF_CHAT_PUBLIC" for event in spectator_events)
    assert len([event for event in spectator_events if event["type"] == "WEREWOLF_WOLF_KILL_VOTE_CAST"]) == len(wolves)
    assert any(event["type"] == "WEREWOLF_WOLF_KILL_DECIDED" for event in spectator_events)
    assert all(event["audience"] == "public" for event in spectator_events)


class DummyJudgeClient:
    async def generate(self, _system_prompt, _user_prompt):
        return """
        {
          "winning_team": "good",
          "losing_team": "werewolf",
          "player_scores": [
            {"player_id": 1, "role": "seer", "team": "good", "score": 7, "reasoning": "拿警徽并推出狼人", "highlights": ["警徽流命中"], "mistakes": []},
            {"player_id": 2, "role": "villager", "team": "good", "score": 4, "reasoning": "正常站边", "highlights": [], "mistakes": []},
            {"player_id": 3, "role": "werewolf", "team": "werewolf", "score": 9, "reasoning": "失败阵营不能得分", "highlights": ["悍跳"], "mistakes": []}
          ],
          "match_summary": "好人胜",
          "key_turning_points": ["P1 带队"],
          "judge_confidence": 0.8
        }
        """


@pytest.mark.asyncio
async def test_ai_judge_zeroes_losing_team_and_allows_different_winner_scores():
    state = create_werewolf_state(list(range(1, 13)), seed=5)
    state.players[1].role = ROLE_SEER
    state.players[2].role = "villager"
    state.players[3].role = ROLE_WEREWOLF
    state.winner = TEAM_GOOD
    state.finished_reason = "all_wolves_eliminated"

    judgement = await judge_werewolf_match(
        state,
        [],
        config=WerewolfJudgeConfig(enabled=True, model="x", api_key="k", base_url="https://example.test"),
        client=DummyJudgeClient(),
    )
    leaderboard = apply_judgement_to_state(state, judgement)

    assert leaderboard[1]["total_score"] == 7
    assert leaderboard[2]["total_score"] == 4
    assert leaderboard[3]["total_score"] == 0
    assert leaderboard[1]["total_score"] != leaderboard[2]["total_score"]


@pytest.mark.asyncio
async def test_ai_judge_fallback_keeps_losing_team_zero():
    state = create_werewolf_state(list(range(1, 13)), seed=6)
    state.winner = TEAM_WEREWOLF
    state.finished_reason = "wolf_parity"

    judgement = await judge_werewolf_match(state, [], config=WerewolfJudgeConfig(enabled=False))
    leaderboard = apply_judgement_to_state(state, judgement)

    for pid, player in state.players.items():
        if player.team == TEAM_WEREWOLF:
            assert leaderboard[pid]["total_score"] == 3
        else:
            assert leaderboard[pid]["total_score"] == 0
