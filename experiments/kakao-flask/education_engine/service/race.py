"""Bounded, cooperative mining competition; all hashes are computed by MiningJob."""

import time
import uuid

from ..core.errors import LabError
from ..core.mining import MiningJob
from ..core.types import NODE_IDS
from .snapshot import mining_wire


class MiningRace:
    def __init__(self, lab, guided=True, mode="learning"):
        self.id = str(uuid.uuid4())
        self.lab = lab
        self.guided = guided
        self.mode = mode
        self.status = "paused"
        self.seq = 0
        self.rotation = 0
        self.enabled = dict.fromkeys(NODE_IDS, True)
        self.single_node = None
        self.jobs = {n: self.new_job(n) for n in NODE_IDS}
        self.completed = []
        self.message = "세 후보가 준비됐습니다. 한 단계 또는 경쟁 시작을 누르세요."
        self.target_reward = None
        self.advance_node = None
        self.advance_start = 0

    def new_job(self, node):
        return MiningJob(str(uuid.uuid4()), node, self.lab.candidate(node), "learning")

    def sync(self):
        for n in NODE_IDS:
            job = self.jobs[n]
            if job.candidate.previous_hash != self.lab.nodes[n].chain[-1].hash:
                self.jobs[n] = self.new_job(n)
                self.jobs[n].message = "새 활성 블록 위에서 후보를 다시 만들었습니다."

    def wire(self):
        return {
            "id": self.id,
            "seq": self.seq,
            "status": self.status,
            "guided": self.guided,
            "mode": self.mode,
            "enabled": self.enabled,
            "singleNode": self.single_node,
            "jobs": {n: mining_wire(j) for n, j in self.jobs.items()},
            "completed": self.completed[-12:],
            "message": self.message,
            "advance": None
            if not self.target_reward
            else {
                "node": self.advance_node,
                "reward": self.target_reward,
                "startHeight": self.advance_start,
                "height": self.lab.nodes[self.advance_node].chain[-1].height,
            },
        }

    def command(self, command, node=None):
        if command not in ("tick", "step", "resume", "pause", "stop", "toggle"):
            raise LabError("RACE_COMMAND", "알 수 없는 경쟁 제어입니다.")
        if command == "toggle":
            if node not in NODE_IDS or self.target_reward:
                raise LabError("RACE_NODE", "빠른 진행 중에는 채굴자 구성을 바꿀 수 없습니다.")
            if self.single_node is not None:
                raise LabError("SINGLE_MINING", "한 노드 채굴 중에는 참여 구성을 바꿀 수 없습니다")
            self.enabled[node] = not self.enabled[node]
        elif command == "pause":
            self.status = "paused"
        elif command == "stop":
            self.status = "stopped"
            self.target_reward = None
            self.message = "중지했습니다. 이미 검증된 블록은 유지됩니다."
        elif command == "resume":
            if self.status == "stopped":
                raise LabError("RACE_STOPPED", "새 경쟁을 준비하세요.", 409)
            if self.single_node is not None:
                self.enabled = {n: n == self.single_node for n in NODE_IDS}
            self.status = "running"
        elif command == "step":
            if self.status != "paused":
                raise LabError("RACE_STATE", "일시정지 후 한 단계를 실행하세요.", 409)
            if self.single_node is not None:
                self.enabled = {n: n == self.single_node for n in NODE_IDS}
            self.compute(single=True)
        elif self.status == "running":
            self.compute(single=False)
        self.seq += 1

    def compute(self, single):
        if not any(self.enabled.values()):
            self.status = "paused"
            self.message = "참여 중인 채굴자가 없습니다. 하나 이상 참여시켜 주세요."
            return
        started = time.perf_counter()
        self.sync()
        while True:
            order = NODE_IDS[self.rotation :] + NODE_IDS[: self.rotation]
            self.rotation = (self.rotation + 1) % len(NODE_IDS)
            for n in order:
                if not self.enabled[n] or (self.single_node is not None and n != self.single_node):
                    continue
                job = self.jobs[n]
                if job.status == "found":
                    job = self.jobs[n] = self.new_job(n)
                job.step()
                if job.status == "found":
                    validation = self.lab.accept_mined(n, job.latest)
                    if not validation.valid:
                        job.fail(validation.message)
                        self.status = "paused"
                        self.message = validation.message
                        return
                    self.completed.append(
                        {
                            "node": n,
                            "hash": job.latest.hash,
                            "height": job.latest.height,
                            "attempts": job.attempts,
                        }
                    )
                    self.completed = self.completed[-12:]
                    self.message = f"채굴자 {n} 블록 발견 · 다른 노드는 수신 후 독립 검증합니다."
                    if self.target_reward:
                        while self.lab.queue:
                            self.lab.deliver_next()
                        target = self.lab.nodes[n].utxos.get(self.target_reward)
                        if target is None:
                            self.status = "paused"
                            self.message = "선택 보상이 활성 체인에서 사라져 진행을 멈췄습니다."
                            self.target_reward = None
                            return
                        if self.lab.nodes[n].chain[-1].height + 1 - target.created_height >= 100:
                            self.status = "paused"
                            self.message = (
                                "성숙 완료 · 선택 보상을 다음 블록의 거래에 사용할 수 있습니다."
                            )
                            self.target_reward = None
                            return
                        self.sync()
                    elif self.guided:
                        self.status = "paused"
                        self.message += " 설명을 위한 전체 일시정지입니다."
                        return
            if single or (self.mode == "learning" and not self.target_reward):
                return
            if (time.perf_counter() - started) * 1000 >= 40:
                return

    def advance(self, node, reward):
        if self.single_node is not None and node != self.single_node:
            raise LabError("SINGLE_MINING", "선택한 노드의 보상만 진행할 수 있습니다")
        if len({n.chain[-1].hash for n in self.lab.nodes.values()}) != 1 or self.lab.queue:
            raise LabError(
                "NOT_SYNCED", "메시지를 모두 전달하고 체인을 동기화한 뒤 진행하세요.", 409
            )
        utxo = self.lab.nodes[node].utxos.get(reward)
        if not utxo or not utxo.coinbase:
            raise LabError("NO_REWARD", "활성 체인의 채굴 보상을 선택하세요.")
        height = self.lab.nodes[node].chain[-1].height
        if height + 1 - utxo.created_height >= 100:
            raise LabError("ALREADY_MATURE", "이미 다음 블록에서 사용할 수 있는 보상입니다.")
        self.target_reward = reward
        self.advance_node = node
        self.advance_start = height
        self.enabled = {n: n == node for n in NODE_IDS}
        self.status = "running"
        self.message = "후속 블록을 실제 채굴·전파·검증합니다. 언제든 중지할 수 있습니다."
