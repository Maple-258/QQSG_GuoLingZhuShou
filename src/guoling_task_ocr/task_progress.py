"""Parse and persist multi-step QQ SG task progress from OCR text."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class ParsedTaskProgress:
    task: str
    current_step: int
    total_steps: int
    role: str | None = None


@dataclass(frozen=True)
class TaskProgress:
    role: str
    task: str
    current_step: int
    total_steps: int
    updated_at: str
    objective_name: str = ""
    required_quantity: int = 0
    unit_price: float | None = None
    total_price: float | None = None
    objective_kind: str = ""
    round_index: int = 1

    @property
    def display_progress(self) -> str:
        if self.total_steps:
            return f"{self.current_step} / {self.total_steps}"
        return f"第 {self.current_step} 步"

    @property
    def display_objective(self) -> str:
        if not self.objective_name:
            return "-"
        label = {"item": "道具", "monster": "怪物", "npc": "NPC"}.get(self.objective_kind)
        return f"{label}：{self.objective_name}" if label else self.objective_name

    @property
    def display_quantity(self) -> str:
        return f"{self.required_quantity} 个" if self.required_quantity else "-"

    @property
    def display_cost(self) -> str:
        if self.objective_kind != "item" or not self.objective_name:
            return "-"
        if self.unit_price is None or self.total_price is None:
            return "待查询"
        return f"{_format_price(self.unit_price)} x {self.required_quantity} = {_format_price(self.total_price)}"


@dataclass(frozen=True)
class TaskObjective:
    """One actionable objective extracted from the task tracking panel."""

    kind: str
    name: str
    current_count: int = 0
    required_count: int = 0
    npc: str = ""

    @property
    def display_quantity(self) -> str:
        if self.required_count:
            return f"需要 {self.required_count} 个（当前 {self.current_count}）"
        return "-"


@dataclass(frozen=True)
class TaskRoundSummary:
    task: str
    round_index: int
    recorded_steps: int
    total_steps: int
    total_cost: float


ROLE_PATTERNS = (
    re.compile(r"(?:当前角色|角色名|角色|人物|玩家)\s*[：:]\s*([\u4e00-\u9fffA-Za-z0-9_]{2,12})"),
    re.compile(r"^\s*([\u4e00-\u9fffA-Za-z0-9_]{2,12})\s*(?:Lv\.?|等级)\s*\d+", re.IGNORECASE),
    re.compile(r"(?:^|\s)(?:Lv\.?)\s*\d{1,3}\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_]{1,15})", re.IGNORECASE),
)
PROGRESS_PATTERN = re.compile(r"(?<!\d)(\d{1,4})\s*[／/]\s*(\d{1,4})(?!\d)")
STEP_PATTERN = re.compile(r"第\s*(\d{1,4})\s*步")
STEP_TASK_PATTERN = re.compile(r"(?<!\d)(\d{1,4})\s*步(?:任务|挑战|修行)?")
TASK_NAME_PATTERN = re.compile(r"([\u4e00-\u9fff]{2,12}(?:任务|修行))")
ROLE_EXCLUSIONS = {
    "当前角色", "任务追踪", "国令慕贤", "元神修行", "高级国令", "任务进度",
    "生命", "气力", "体力", "等级", "经验", "技能", "NPC", "PK",
}
NAMED_STEP_TASKS = {
    "高级国令慕贤": ("高级国令慕贤", "高级国令募贤", "高级国令夺贤"),
    "国令慕贤": ("国令慕贤", "国令募贤", "国令夺贤"),
    "元神修行": ("元神修行",),
    "修行之途": ("修行之途", "200步任务", "200步"),
}
TASK_STEP_LIMITS = {
    "高级国令慕贤": 8,
    "国令慕贤": 8,
    "元神修行": 30,
    "修行之途": 200,
}
OBJECTIVE_GRADE_PREFIX_PATTERN = re.compile(
    r"^(\u4e0b\u54c1|\u4e2d\u54c1|\u4e0a\u54c1|\u6781\u54c1|\u521d\u7ea7|\u4e2d\u7ea7|\u9ad8\u7ea7|\u7279\u7ea7|[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d]\u9636)"
)
NPC_PATTERN = re.compile(r"NPC\s*[：:]\s*([\u4e00-\u9fffA-Za-z0-9_]{1,16})", re.IGNORECASE)
NPC_LABEL_PATTERN = re.compile(r"NPC\s*[：:]\s*$", re.IGNORECASE)
OBJECTIVE_PATTERNS = (
    (
        "monster",
        re.compile(
            r"(?:消?灭|击败|击杀|打败|剿灭|杀死)(?:怪物)?\s*[：:]?\s*"
            r"(?P<name>[\u4e00-\u9fffA-Za-z0-9_\-()（）]{1,16}?)(?:\s*(?P<current>\d{1,4})\s*[／/]\s*(?P<required>\d{1,4}))?\s*$"
        ),
    ),
    (
        "item",
        re.compile(
            r"(?:收集|上交|提交|交付|需要|寻找|消耗|物品|道具|材料)(?:物品|道具|材料)?\s*[：:]?\s*"
            r"(?P<name>[\u4e00-\u9fffA-Za-z0-9_\-()（）]{1,16}?)(?:\s*(?P<current>\d{1,4})\s*[／/]\s*(?P<required>\d{1,4}))?\s*$"
        ),
    ),
)


def parse_task_progress(lines: list[str]) -> ParsedTaskProgress | None:
    """Extract a role, task name, and current step from task-panel OCR lines."""
    cleaned_lines = [line.strip() for line in lines if line and line.strip()]
    if not cleaned_lines:
        return None
    text = "\n".join(cleaned_lines)
    task, total_hint = _find_task(text)
    if not task:
        return None
    current_step, total_steps = _find_steps(cleaned_lines, task, total_hint)
    if current_step is None:
        return None
    return ParsedTaskProgress(task, current_step, total_steps or 0, find_role_name(cleaned_lines))


def find_task_name(lines: list[str]) -> str | None:
    """Identify a supported multi-step task even when its step OCR is missing."""
    cleaned_lines = [line.strip() for line in lines if line and line.strip()]
    task, _total_hint = _find_task("\n".join(cleaned_lines))
    return task or None


def canonical_task_name(task: str) -> str:
    """Keep community shorthand and older saved names under the official task name."""
    normalized = task.strip()
    return "修行之途" if normalized == "200步任务" else normalized


def parse_task_objective(lines: list[str]) -> TaskObjective | None:
    """Extract the required item/monster quantity, or fall back to the task NPC."""
    cleaned_lines = [line.strip() for line in lines if line and line.strip()]
    npc = ""
    for index, line in enumerate(cleaned_lines):
        match = NPC_PATTERN.search(line)
        if match:
            npc = match.group(1).strip()
            break
        if NPC_LABEL_PATTERN.search(line) and index + 1 < len(cleaned_lines):
            possible_npc = cleaned_lines[index + 1].strip()
            if re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9_]{1,16}", possible_npc):
                npc = possible_npc
                break

    for line in cleaned_lines:
        for kind, pattern in OBJECTIVE_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            name = match.group("name").strip()
            if not name:
                continue
            return TaskObjective(
                kind=kind,
                name=name,
                current_count=int(match.group("current") or 0),
                required_count=int(match.group("required") or 0),
                npc=npc,
            )
    if npc:
        return TaskObjective(kind="npc", name=npc, npc=npc)
    return None


def _find_task(text: str) -> tuple[str, int | None]:
    # The stable "高级国令" prefix survives OCR far more reliably than the
    # two-character task suffix (for example 慕贤 can become 募贤 or 夺贤).
    if "高级国令" in text:
        return "高级国令慕贤", None
    for task, variants in NAMED_STEP_TASKS.items():
        if any(variant in text for variant in variants):
            return task, None
    step_task = STEP_TASK_PATTERN.search(text)
    if step_task:
        total = int(step_task.group(1))
        if total >= 2:
            return f"{total}步任务", total
    task_name = TASK_NAME_PATTERN.search(text)
    if task_name:
        return task_name.group(1), None
    return "", None


def _task_name_match(task: str, line: str) -> re.Match[str] | None:
    if task == "高级国令慕贤":
        return re.search(r"高级国令(?:[\u4e00-\u9fff]{0,2})?", line)
    for variant in NAMED_STEP_TASKS.get(task, (task,)):
        match = re.search(re.escape(variant), line)
        if match:
            return match
    return None


def _normalise_task_step(task: str, step: int) -> int | None:
    """Validate a numbered step and repair a common leading-1 OCR error."""
    limit = TASK_STEP_LIMITS.get(task)
    if step < 1:
        return None
    if limit is None or step <= limit:
        return step
    # 国令慕贤 has only eight steps. Paddle can join a nearby stroke before a
    # single digit, producing values such as 12 for the actual step 2.
    if limit < 10 and 10 <= step <= 19 and 1 <= step % 10 <= limit:
        return step % 10
    return None


def _find_steps(lines: list[str], task: str, total_hint: int | None) -> tuple[int | None, int | None]:
    candidates: list[tuple[int, int, bool]] = []
    for line in lines:
        # Item and monster quantities (for example "道具:下品复原灵石 0/40")
        # describe a requirement, never the numbered task step.
        if any(pattern.search(line) for _kind, pattern in OBJECTIVE_PATTERNS) or any(
            label in line for label in ("道具", "物品", "材料", "怪物")
        ):
            continue
        has_progress_context = any(term in line for term in ("进度", "步骤", "第", "完成", "当前"))
        for match in PROGRESS_PATTERN.finditer(line):
            current, total = (int(value) for value in match.groups())
            if total and current <= total:
                candidates.append((current, total, has_progress_context))

    def result_for_explicit_step(step: int) -> tuple[int | None, int | None]:
        # A labelled progress fraction is more reliable than a digit attached
        # to the task title, where OCR can accidentally add a leading stroke.
        contextual_candidates = [
            (current, total)
            for current, total, contextual in candidates
            if contextual
            and _normalise_task_step(task, current) is not None
            and (TASK_STEP_LIMITS.get(task) is None or total <= TASK_STEP_LIMITS[task])
        ]
        if contextual_candidates:
            current, total = max(contextual_candidates, key=lambda entry: entry[1])
            return _normalise_task_step(task, current), total
        normalized_step = _normalise_task_step(task, step)
        return normalized_step, total_hint

    for line in lines:
        step = STEP_PATTERN.search(line)
        if step:
            return result_for_explicit_step(int(step.group(1)))

    # QQ SG commonly displays the step on its own line immediately after the
    # task name, for example: "高级国令慕贤" followed by "1".
    for index, line in enumerate(lines):
        task_match = _task_name_match(task, line)
        if task_match is None:
            continue
        suffix = line[task_match.end():]
        inline_step = re.search(r"(?:第\s*)?(\d{1,4})\s*(?:步)?\s*$", suffix)
        if inline_step:
            return result_for_explicit_step(int(inline_step.group(1)))
        if index + 1 < len(lines) and re.fullmatch(r"\d{1,4}", lines[index + 1].strip()):
            return result_for_explicit_step(int(lines[index + 1].strip()))

    contextual_candidates = [
        entry for entry in candidates
        if entry[2]
        and _normalise_task_step(task, entry[0]) is not None
        and (TASK_STEP_LIMITS.get(task) is None or entry[1] <= TASK_STEP_LIMITS[task])
    ]
    if contextual_candidates:
        if total_hint:
            for current, total, _context in contextual_candidates:
                if total == total_hint:
                    return _normalise_task_step(task, current), total
        contextual_candidates.sort(key=lambda entry: entry[1], reverse=True)
        return _normalise_task_step(task, contextual_candidates[0][0]), contextual_candidates[0][1]

    # Unlabelled values can still be task progress for long multi-step tasks,
    # but values such as an item requirement "0/1" must never become step 0.
    fallback_candidates = [
        entry for entry in candidates
        if entry[1] >= 2
        and _normalise_task_step(task, entry[0]) is not None
        and (TASK_STEP_LIMITS.get(task) is None or entry[1] <= TASK_STEP_LIMITS[task])
    ]
    if fallback_candidates:
        fallback_candidates.sort(key=lambda entry: entry[1], reverse=True)
        return _normalise_task_step(task, fallback_candidates[0][0]), fallback_candidates[0][1]
    return None, total_hint


def find_role_name(lines: list[str], allow_unlabelled: bool = False) -> str | None:
    """Find a role name in OCR lines from either a task panel or player HUD."""
    for line in lines:
        for pattern in ROLE_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            role = match.group(1).strip()
            if role not in ROLE_EXCLUSIONS:
                return role

    if not allow_unlabelled:
        return None
    for line in lines:
        role = re.sub(r"\s+", "", line)
        if is_role_name_candidate(role):
            return role
    return None


def is_role_name_candidate(value: str) -> bool:
    """Reject OCR mistakes such as ``Sv120`` before using a HUD text as a role."""
    role = re.sub(r"\s+", "", value)
    if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_]{1,15}", role):
        return False
    if role in ROLE_EXCLUSIONS or role.lower().startswith("lv"):
        return False
    # Paddle may add one or two letters before a level read, for example
    # ``gLv120``. It is still not a character name.
    return not re.fullmatch(r"[A-Za-z]{0,2}[Ll1IiSs5](?:[vVyY])?\d{1,4}", role)


def task_record_key(record: TaskProgress) -> tuple[str, str, int, int]:
    """Return the stable identity for one observed task step."""
    return record.role, record.task, record.round_index, record.current_step


def load_task_progress(progress_path: Path) -> dict[tuple[str, str, int, int], TaskProgress]:
    """Load valid saved rows, ignoring a damaged or older data file."""
    try:
        payload = json.loads(progress_path.read_text(encoding="utf-8"))
        rows = payload.get("records", [])
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(rows, list):
        return {}

    records: dict[tuple[str, str, int, int], TaskProgress] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role", "")).strip()
        task = canonical_task_name(str(row.get("task", "")))
        try:
            current_step = int(row.get("current_step", 0))
            total_steps = int(row.get("total_steps", 0))
            round_index = max(1, int(row.get("round_index", 1)))
        except (TypeError, ValueError):
            continue
        if not role or not task or current_step < 0 or total_steps < 0:
            continue
        objective_name = str(row.get("objective_name", "")).strip()
        objective_kind = str(row.get("objective_kind", "")).strip()
        try:
            required_quantity = max(0, int(row.get("required_quantity", 0)))
            unit_price = _optional_price(row.get("unit_price"))
            total_price = _optional_price(row.get("total_price"))
        except (TypeError, ValueError):
            continue
        # Versions before monster tracking stored only priced item objectives.
        if not objective_kind and objective_name and required_quantity:
            objective_kind = "item"
        record = TaskProgress(
            role, task, current_step, total_steps, str(row.get("updated_at", "")).strip(),
            objective_name, required_quantity, unit_price, total_price, objective_kind, round_index,
        )
        records[task_record_key(record)] = record
    return records


def save_task_progress(records: dict[tuple[str, str, int, int], TaskProgress], progress_path: Path) -> None:
    """Persist task rows in a stable order for inspection and recovery."""
    payload = {"records": [asdict(record) for _key, record in sorted(records.items())]}
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def filter_task_progress_records(
    records: dict[tuple[str, str, int, int], TaskProgress],
    role: str = "",
    task: str = "",
    round_index: int | None = None,
    date: str = "",
) -> list[TaskProgress]:
    """Return rows matching optional role, task, round, and ISO date filters."""
    return [
        record
        for record in records.values()
        if (not role or record.role == role)
        and (not task or record.task == task)
        and (round_index is None or record.round_index == round_index)
        and (not date or record.updated_at.startswith(date))
    ]


def record_task_progress(
    records: dict[tuple[str, str, int, int], TaskProgress],
    role: str,
    parsed: ParsedTaskProgress,
    objective: TaskObjective | None = None,
    unit_price: float | None = None,
) -> TaskProgress:
    """Store the latest OCR observation for one role, task, and task step."""
    if parsed.task != canonical_task_name(parsed.task):
        parsed = ParsedTaskProgress(
            canonical_task_name(parsed.task), parsed.current_step, parsed.total_steps, parsed.role
        )
    is_quantity_objective = (
        objective is not None
        and objective.kind in {"item", "monster"}
        and objective.required_count > 0
    )
    is_npc_objective = objective is not None and objective.kind == "npc" and bool(objective.name)
    is_recordable_objective = is_quantity_objective or is_npc_objective
    is_item_objective = is_quantity_objective and objective.kind == "item"
    required_quantity = objective.required_count if is_quantity_objective else 0
    normalized_price = _optional_price(unit_price) if is_item_objective else None
    round_index = _record_round_index(records, role, parsed)
    record = TaskProgress(
        role=role,
        task=parsed.task,
        current_step=parsed.current_step,
        total_steps=parsed.total_steps,
        updated_at=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
        objective_name=objective.name if is_recordable_objective else "",
        required_quantity=required_quantity,
        unit_price=normalized_price,
        total_price=normalized_price * required_quantity if normalized_price is not None else None,
        objective_kind=objective.kind if is_recordable_objective else "",
        round_index=round_index,
    )
    records[task_record_key(record)] = record
    return record


def _task_round_size(task: str, total_steps: int) -> int:
    return TASK_STEP_LIMITS.get(task, total_steps)


def _record_round_index(
    records: dict[tuple[str, str, int, int], TaskProgress], role: str, parsed: ParsedTaskProgress,
) -> int:
    existing = [record for record in records.values() if record.role == role and record.task == parsed.task]
    if not existing:
        return 1
    latest_round = max(record.round_index for record in existing)
    round_size = _task_round_size(parsed.task, parsed.total_steps)
    latest_steps = {
        record.current_step for record in existing
        if record.round_index == latest_round
    }
    if parsed.current_step == 1:
        # A task panel never returns to step 1 within the same run.  Therefore
        # seeing step 1 after any later observed step is a new round even when
        # OCR was not active for every intermediate step.
        has_later_step = any(step > 1 for step in latest_steps)
        completed_round = round_size and set(range(1, round_size + 1)).issubset(latest_steps)
        if has_later_step or completed_round:
            return latest_round + 1
    return latest_round


def summarize_task_rounds(
    records: dict[tuple[str, str, int, int], TaskProgress], role: str,
) -> list[TaskRoundSummary]:
    """Return step coverage and item cost for every recorded task round."""
    summaries: list[TaskRoundSummary] = []
    tasks = sorted({record.task for record in records.values() if record.role == role})
    for task in tasks:
        task_records = [record for record in records.values() if record.role == role and record.task == task]
        for round_index in sorted({record.round_index for record in task_records}):
            current_round = [record for record in task_records if record.round_index == round_index]
            total_steps = _task_round_size(task, max((record.total_steps for record in current_round), default=0))
            total_cost = sum(record.total_price or 0 for record in current_round if record.objective_kind == "item")
            summaries.append(TaskRoundSummary(task, round_index, len({record.current_step for record in current_round}), total_steps, total_cost))
    return summaries


def summarize_latest_task_rounds(
    records: dict[tuple[str, str, int, int], TaskProgress], role: str,
) -> list[TaskRoundSummary]:
    """Return the latest round of each task for backwards-compatible callers."""
    summaries = summarize_task_rounds(records, role)
    latest_by_task: dict[str, TaskRoundSummary] = {}
    for summary in summaries:
        latest_by_task[summary.task] = summary
    return [latest_by_task[task] for task in sorted(latest_by_task)]


def infer_unread_task_step(
    records: dict[tuple[str, str, int, int], TaskProgress],
    role: str,
    task: str,
    objective: TaskObjective | None,
) -> int | None:
    """Conservatively recover a missing step from the active task round.

    This only runs when the task name and a concrete target were recognized,
    but the step number was not. Repeating an already recorded target refreshes
    its row; a new item, monster, or NPC target advances one sequential step
    in the current round.
    """
    if objective is None or objective.kind not in {"item", "monster", "npc"} or not objective.name:
        return None
    task_records = [record for record in records.values() if record.role == role and record.task == task]
    if not task_records:
        return None
    round_index = max(record.round_index for record in task_records)
    current_round = [record for record in task_records if record.round_index == round_index]
    matching_steps = [
        record.current_step
        for record in current_round
        if record.objective_kind == objective.kind
        and objective_names_match(record.objective_name, objective.name)
        and record.required_quantity == objective.required_count
    ]
    if matching_steps:
        return max(matching_steps)
    round_size = _task_round_size(task, max((record.total_steps for record in current_round), default=0))
    latest_step = max((record.current_step for record in current_round), default=0)
    if round_size and latest_step < round_size:
        return latest_step + 1
    return None


def objective_names_match(left: str, right: str) -> bool:
    """Treat a small, unambiguous OCR drift as the previously seen target."""
    normalized_left = re.sub(r"\s+", "", left)
    normalized_right = re.sub(r"\s+", "", right)
    if normalized_left == normalized_right:
        return True
    if len(normalized_left) < 3 or len(normalized_right) < 3:
        return False
    left_grade = _objective_grade_prefix(normalized_left)
    right_grade = _objective_grade_prefix(normalized_right)
    if left_grade != right_grade:
        return False
    maximum_distance = 1 if min(len(normalized_left), len(normalized_right)) <= 5 else 2
    if abs(len(normalized_left) - len(normalized_right)) > maximum_distance:
        return False
    return _edit_distance(normalized_left, normalized_right) <= maximum_distance


def _objective_grade_prefix(name: str) -> str:
    match = OBJECTIVE_GRADE_PREFIX_PATTERN.match(name)
    return match.group(1) if match else ""


def _edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_character != right_character),
            ))
        previous = current
    return previous[-1]


def _optional_price(value: object) -> float | None:
    if value in (None, ""):
        return None
    price = float(value)
    return price if price >= 0 else None


def _format_price(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")
