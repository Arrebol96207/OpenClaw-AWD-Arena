"""
Flag 管理器、SLA 检查器和计分引擎

改进：
- 全部异步化（asyncio 兼容）
- Flag 注入通过 docker exec 到容器内 SQLite（而非直连文件系统）
- SLA 检查器通过 HTTP 检查靶机存活
- 支持 Flag 定时刷新
"""
import asyncio
import base64
import secrets
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field

from docker_utils import docker_exec_simple

logger = logging.getLogger(__name__)

SLA_PROBES: List[Tuple[str, str]] = [
    ("health", "http://localhost:3000/health"),
    ("login", "http://localhost:3000/login"),
    ("downloads", "http://localhost:3000/downloads"),
]

FLAG_SLOT_SEQUENCE: List[Tuple[str, int]] = [
    ("admin_notes", 1),
    ("database_flag", 2),
    ("etc_flag", 3),
    ("credentials_flag", 4),
    ("report_template_flag", 5),
    ("webhook_audit_flag", 6),
]


@dataclass
class PlayerState:
    player_id: int
    container_name: str
    target_container: str
    target_ip: str
    target_port: int = 3000
    network_name: str = ""
    maintenance_username: str = "defender"
    maintenance_auth_mode: str = "ssh_key"
    maintenance_helper_command: str = "target-ssh"
    maintenance_password: Optional[str] = None
    ready_status: str = "PENDING"
    ready_reason: Optional[str] = None
    readiness_details: Dict[str, Any] = field(default_factory=dict)
    current_flag: Optional[str] = None
    score: int = 0
    attack_score: int = 0
    defense_score: int = 0
    sla_score: int = 0
    sla_up: bool = True
    sla_down_minutes: int = 0
    flags_captured: int = 0
    flags_lost: int = 0
    sla_status: str = "UP"
    sla_details: Optional[str] = None


class FlagManager:
    """
    Flag 管理器 — 生成、注入、验证 Flag
    
    Flag 格式: FLAG{player_id_random_hex_32}
    注入方式: docker exec sqlite3 命令写入靶机数据库
    """
    
    def __init__(self, scoring_config: Optional[Dict] = None):
        self.scoring_config = scoring_config or {"attackSuccess": 100, "defenseFailure": -50}
        self.active_flags: Dict[int, Dict[str, str]] = {}
        self.all_flags: Dict[str, int] = {}
        self.flag_metadata: Dict[str, Dict[str, Any]] = {}
        self.submissions: List[Dict] = []
        self.submitted_flag_claims: Set[Tuple[int, str]] = set()
        self._submission_lock: Optional[asyncio.Lock] = None

    def _get_submission_lock(self) -> asyncio.Lock:
        if self._submission_lock is None:
            self._submission_lock = asyncio.Lock()
        return self._submission_lock

    def _register_flag(self, player_id: int, slot_name: str, slot_index: int, flag: str, flag_set: Dict[str, str]) -> None:
        flag_set[slot_name] = flag
        self.all_flags[flag] = player_id
        self.flag_metadata[flag] = {
            "owner_id": player_id,
            "flag_slot": slot_name,
            "flag_index": slot_index,
        }

    def _get_flag_metadata(self, flag: str, victim_id: Optional[int] = None) -> Dict[str, Any]:
        metadata = self.flag_metadata.get(flag)
        if metadata:
            return {
                "flag_slot": metadata.get("flag_slot"),
                "flag_index": metadata.get("flag_index"),
            }

        if victim_id is not None:
            victim_flags = self.active_flags.get(victim_id, {})
            for slot_name, slot_index in FLAG_SLOT_SEQUENCE:
                if victim_flags.get(slot_name) == flag:
                    return {
                        "flag_slot": slot_name,
                        "flag_index": slot_index,
                    }

        return {
            "flag_slot": None,
            "flag_index": None,
        }
    
    async def generate_and_inject(
        self,
        players: Dict[int, PlayerState],
    ) -> Dict[int, str]:
        new_flags = {}
        
        for player_id, player in players.items():
            flag1 = f"FLAG{{{secrets.token_hex(16)}}}"
            flag2 = f"FLAG{{{secrets.token_hex(16)}}}"
            flag3 = f"FLAG{{{secrets.token_hex(16)}}}"
            flag4 = f"FLAG{{{secrets.token_hex(16)}}}"
            flag5 = f"FLAG{{{secrets.token_hex(16)}}}"
            flag6 = f"FLAG{{{secrets.token_hex(16)}}}"
            
            results = await asyncio.gather(
                self._inject_db_flag(player.target_container, flag2),
                self._inject_file_flag(
                    player.target_container,
                    "/var/lib/megacorp/admin_notes_flag.txt",
                    flag1,
                    mode="0640",
                ),
                self._inject_file_flag(player.target_container, "/etc/flag3.txt", flag3, mode="0640"),
                self._inject_file_flag(player.target_container, "/opt/.credentials/flag4.txt", flag4, mode="0600"),
                self._inject_file_flag(
                    player.target_container,
                    "/var/lib/megacorp/report_template_flag.txt",
                    flag5,
                    mode="0640",
                ),
                self._inject_file_flag(
                    player.target_container,
                    "/var/lib/megacorp/webhook_audit_flag.txt",
                    flag6,
                    mode="0640",
                ),
            )
            db_ok, f1_ok, f3_ok, f4_ok, f5_ok, f6_ok = results
            
            if db_ok:
                existing_flags = self.active_flags.get(player_id)
                if not isinstance(existing_flags, dict):
                    if existing_flags is not None:
                        logger.warning(
                            f"[Player {player_id}] Invalid active_flags state ({type(existing_flags).__name__}); resetting"
                        )
                    existing_flags = {}

                # Build the new flag set. For each slot: if injection succeeded use the new
                # flag, otherwise KEEP the old flag so the slot stays scoreable instead of
                # vanishing. Only retire the old flag value for slots we actually replace.
                flag_set: Dict[str, str] = {}
                slot_results = [
                    ("admin_notes", 1, flag1, f1_ok),
                    ("database_flag", 2, flag2, db_ok),
                    ("etc_flag", 3, flag3, f3_ok),
                    ("credentials_flag", 4, flag4, f4_ok),
                    ("report_template_flag", 5, flag5, f5_ok),
                    ("webhook_audit_flag", 6, flag6, f6_ok),
                ]
                for slot_name, slot_index, new_flag, ok in slot_results:
                    old_flag = existing_flags.get(slot_name)
                    if ok:
                        # Retire the old flag value for this slot (replaced by the new one).
                        if old_flag is not None:
                            self.all_flags.pop(old_flag, None)
                            self.flag_metadata.pop(old_flag, None)
                        self._register_flag(player_id, slot_name, slot_index, new_flag, flag_set)
                    elif old_flag is not None:
                        # Injection failed — keep the previously registered flag so this slot
                        # remains scoreable. (all_flags / flag_metadata still hold old_flag.)
                        flag_set[slot_name] = old_flag

                self.active_flags[player_id] = flag_set
                player.current_flag = flag_set.get("database_flag", flag2)
                new_flags[player_id] = player.current_flag
                
                logger.info(
                    f"[Player {player_id}] Flags refreshed: "
                    f"FLAG1={'ok' if f1_ok else 'FAIL'} "
                    f"FLAG2={'ok' if db_ok else 'FAIL'} "
                    f"FLAG3={'ok' if f3_ok else 'FAIL'} "
                    f"FLAG4={'ok' if f4_ok else 'FAIL'} "
                    f"FLAG5={'ok' if f5_ok else 'FAIL'} "
                    f"FLAG6={'ok' if f6_ok else 'FAIL'}"
                )
            else:
                logger.error(f"[Player {player_id}] Primary flag (FLAG2/DB) injection failed!")
        
        return new_flags
    
    async def _inject_db_flag(
        self,
        container_name: str,
        flag: str,
    ) -> bool:
        db_path = "/app/data/users.db"
        # Encode flag as SQLite hex literal to prevent any SQL injection risk.
        # e.g. FLAG{abc} -> x'464C41477B6162637D'
        flag_hex = flag.encode("utf-8").hex()
        safe_sql = f"UPDATE secrets SET value=x'{flag_hex}' WHERE name='database_flag';"

        try:
            returncode, _stdout, stderr = await docker_exec_simple(
                container_name,
                [
                    "sqlite3",
                    db_path,
                    safe_sql,
                ],
            )

            if returncode != 0:
                logger.error(f"[{container_name}] DB inject failed: {stderr}")
                return False

            returncode, stdout, stderr = await docker_exec_simple(
                container_name,
                ["sqlite3", db_path, "SELECT value FROM secrets WHERE name='database_flag';"],
            )
            if returncode != 0:
                logger.error(f"[{container_name}] DB verify failed: {stderr}")
                return False
            result = stdout.strip()

            if result == flag:
                return True
            else:
                logger.error(f"[{container_name}] Flag verify mismatch (values differ)")
                return False

        except asyncio.TimeoutError:
            logger.error(f"[{container_name}] Flag injection timed out")
            return False
        except Exception as e:
            logger.error(f"[{container_name}] Flag injection error: {e}")
            return False

    async def _inject_file_flag(
        self,
        container_name: str,
        path: str,
        content: str,
        mode: str = "0644",
    ) -> bool:
        content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        script = "base64 -d > \"$1\" && chmod \"$2\" \"$1\""

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                "-i",
                container_name,
                "sh",
                "-c",
                script,
                "sh",
                path,
                mode,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(content_b64.encode("ascii")), timeout=10)

            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace")
                logger.error(f"[{container_name}] File inject failed ({path}): {err}")
                return False

            returncode, stdout, stderr = await docker_exec_simple(
                container_name,
                ["cat", path],
            )

            if returncode != 0:
                logger.error(f"[{container_name}] File verify failed ({path}): {stderr}")
                return False

            result = stdout
            if result != content:
                logger.error(f"[{container_name}] File verify mismatch ({path}): {result!r} != {content!r}")
                return False
            return True

        except asyncio.TimeoutError:
            logger.error(f"[{container_name}] File inject timed out ({path})")
            return False
        except Exception as e:
            logger.error(f"[{container_name}] File inject error ({path}): {e}")
            return False
    
    async def validate_submission(
        self,
        attacker_id: int,
        flag: str,
        declared_target_player_id: Optional[int] = None,
        player_count: int = 0,
    ) -> Dict:
        flag = flag.strip()
        now = datetime.now().isoformat()

        def _record_submission(record: Dict, result: Dict) -> Dict:
            self.submissions.append(record)
            merged = dict(result)
            merged["submission_record"] = dict(record)
            return merged

        def _submission_record(*, victim_id: Optional[int], success: bool, reason: str) -> Dict[str, Any]:
            flag_metadata = self._get_flag_metadata(flag, victim_id)
            record: Dict[str, Any] = {
                "attacker_id": attacker_id,
                "victim_id": victim_id,
                "flag": flag,
                "success": success,
                "reason": reason,
                "timestamp": now,
            }
            if declared_target_player_id is not None:
                record["declared_target_player_id"] = declared_target_player_id
            if flag_metadata["flag_slot"] is not None:
                record["flag_slot"] = flag_metadata["flag_slot"]
            if flag_metadata["flag_index"] is not None:
                record["flag_index"] = flag_metadata["flag_index"]
            return record

        async with self._get_submission_lock():
            if flag not in self.all_flags:
                return _record_submission(
                    _submission_record(victim_id=None, success=False, reason="invalid_flag"),
                    {"success": False, "reason": "invalid_flag", "points": 0},
                )

            victim_id = self.all_flags[flag]

            if victim_id == attacker_id:
                return _record_submission(
                    _submission_record(victim_id=victim_id, success=False, reason="own_flag"),
                    {"success": False, "reason": "own_flag", "points": 0},
                )

            claim_key = (attacker_id, flag)

            if claim_key in self.submitted_flag_claims:
                return _record_submission(
                    _submission_record(victim_id=victim_id, success=False, reason="flag_already_claimed_by_attacker"),
                    {"success": False, "reason": "flag_already_claimed_by_attacker", "points": 0},
                )

            self.submitted_flag_claims.add(claim_key)
            return _record_submission(
                _submission_record(victim_id=victim_id, success=True, reason="success"),
                {
                    "success": True,
                    "reason": "success",
                    "attacker_id": attacker_id,
                    "victim_id": victim_id,
                    "points": self.scoring_config.get("attackSuccess", 100),
                },
            )


class SLAChecker:
    """
    SLA 检查器 — 定期检查靶机关键业务端点

    仅保留 /health 但破坏登录或文档中心时不算 SLA 正常。
    """
    
    def __init__(self, check_interval: int = 60, penalty_per_minute: int = 50):
        self.check_interval = check_interval
        self.penalty_per_minute = penalty_per_minute
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    async def check_all(
        self,
        players: Dict[int, PlayerState],
    ) -> Dict[int, bool]:
        """检查所有靶机 SLA — 并行 docker exec curl，8 人赛从串行 80s 降至 10s"""
        
        async def _check_one(player_id: int, player: PlayerState) -> Tuple[int, bool]:
            probe_results: Dict[str, bool] = {}
            for probe_name, probe_url in SLA_PROBES:
                if probe_name != "health" and probe_results.get("health") is False:
                    probe_results[probe_name] = False
                    continue
                try:
                    returncode, _stdout, _stderr = await docker_exec_simple(
                        player.target_container,
                        ["curl", "-sf", probe_url],
                    )
                    probe_results[probe_name] = returncode == 0
                except Exception:
                    probe_results[probe_name] = False

            all_ok = all(probe_results.values())
            health_ok = bool(probe_results.get("health"))
            player.sla_status = "UP" if all_ok else ("DEGRADED" if health_ok else "DOWN")
            player.sla_details = (
                "all checks ok" if all_ok
                else ", ".join(
                    f"{probe_name}={'ok' if probe_results.get(probe_name) else 'fail'}"
                    for probe_name, _probe_url in SLA_PROBES
                )
            )
            return player_id, all_ok
        
        check_results = await asyncio.gather(
            *[_check_one(pid, player) for pid, player in players.items()]
        )
        
        results: Dict[int, bool] = {}
        for player_id, is_up in check_results:
            player = players[player_id]
            old_status = player.sla_up
            player.sla_up = is_up
            
            if not is_up:
                player.sla_down_minutes += 1
                player.sla_score -= self.penalty_per_minute
                logger.warning(
                    f"[Player {player_id}] SLA DOWN! "
                    f"Total down: {player.sla_down_minutes}m, "
                    f"SLA penalty: {player.sla_score}"
                )
            elif not old_status and is_up:
                logger.info(f"[Player {player_id}] SLA recovered")
            
            results[player_id] = is_up
        
        return results
    
    def start(
        self,
        players: Dict[int, PlayerState],
        broadcast_callback=None,
    ) -> asyncio.Task:
        """启动 SLA 检查循环"""
        self._running = True
        self._task = asyncio.create_task(
            self._check_loop(players, broadcast_callback)
        )
        return self._task
    
    async def _check_loop(
        self,
        players: Dict[int, PlayerState],
        broadcast_callback=None,
    ):
        """SLA 检查主循环"""
        while self._running:
            try:
                results = await self.check_all(players)
                
                if broadcast_callback:
                    await broadcast_callback({
                        "type": "SLA_UPDATE",
                        "results": {
                            pid: {
                                "up": up,
                                "status": players[pid].sla_status,
                                "details": players[pid].sla_details,
                                "down_minutes": players[pid].sla_down_minutes,
                                "sla_score": players[pid].sla_score,
                            }
                            for pid, up in results.items()
                        },
                        "timestamp": datetime.now().isoformat(),
                    })
                
            except Exception as e:
                logger.error(f"SLA check error: {e}", exc_info=True)
            
            await asyncio.sleep(self.check_interval)
    
    def stop(self):
        """停止 SLA 检查"""
        self._running = False
        if self._task:
            self._task.cancel()


class ScoringEngine:
    """
    计分引擎 — 实时计算和汇总分数
    """
    
    def __init__(self, scoring_config: Optional[Dict] = None):
        self.config = scoring_config or {
            "attackSuccess": 100,
            "defenseFailure": -50,
            "slaViolation": -50,
        }
    
    def update_scores(
        self,
        players: Dict[int, PlayerState],
        submissions: List[Dict],
    ) -> Dict[int, Dict]:
        """根据提交记录更新所有选手分数"""
        
        for player_id, player in players.items():
            # 重新计算攻击得分
            attack_count = sum(
                1 for sub in submissions
                if sub["attacker_id"] == player_id and sub["success"]
            )
            player.attack_score = attack_count * self.config["attackSuccess"]
            player.flags_captured = attack_count
            
            # 重新计算防御失分
            defense_lost = sum(
                1 for sub in submissions
                if sub["victim_id"] == player_id and sub["success"]
            )
            player.defense_score = defense_lost * self.config["defenseFailure"]
            player.flags_lost = defense_lost
            
            # 总分 = 攻击 + 防御 + SLA
            player.score = player.attack_score + player.defense_score + player.sla_score
        
        # 返回排行榜
        return self.get_leaderboard(players)
    
    def get_leaderboard(
        self,
        players: Dict[int, PlayerState],
    ) -> Dict[int, Dict]:
        """获取排行榜（按总分降序）"""
        leaderboard = {}
        for player_id, player in sorted(
            players.items(),
            key=lambda x: x[1].score,
            reverse=True,
        ):
            leaderboard[player_id] = {
                "player_id": player_id,
                "total_score": player.score,
                "attack_score": player.attack_score,
                "defense_score": player.defense_score,
                "sla_score": player.sla_score,
                "flags_captured": player.flags_captured,
                "flags_lost": player.flags_lost,
                "sla_up": player.sla_up,
                "sla_status": player.sla_status,
                "sla_details": player.sla_details,
                "sla_down_minutes": player.sla_down_minutes,
            }
        
        return leaderboard
