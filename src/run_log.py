


import re
import textwrap
from datetime import datetime
from pathlib import Path


# long texts are wrapped to this width so the terminal stays readable
WRAP_WIDTH = 120

# where save_txt() writes by default; the app points this next to the executable
LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


def _wrap(text, first_prefix: str, rest_prefix: str) -> list:
    """
    Wrap one long text into terminal-friendly prefixed lines.

    Args:
        text: The text to wrap (anything, rendered as str).
        first_prefix (str): Prefix of the first line, e.g. "    why: ".
        rest_prefix (str): Prefix of the continuation lines, normally
            spaces of the same width so the text stays aligned.

    Returns:
        list[str]: The wrapped lines, prefixes included. An empty text
        gives one line with just "-" after the prefix.
    """

    stripped_text = str(text).strip()
    if stripped_text == "":
        return [first_prefix + "-"]

    available_width = WRAP_WIDTH - len(rest_prefix)
    if available_width < 20:
        available_width = 20

    wrapped = textwrap.wrap(stripped_text, width=available_width)

    lines = [first_prefix + wrapped[0]]
    for continuation in wrapped[1:]:
        lines.append(rest_prefix + continuation)
    return lines


class StepLog:
    """
    One recorded step of a run: an LLM call, a tool call or a note.

    Which fields are filled depends on the kind ("llm", "tool" or
    "note"); the fields of the other kinds stay None.
    """

    def __init__(self, kind: str, step_name: str):
        self.kind = kind
        self.step_name = step_name
        self.recorded_at = datetime.now()

        # llm fields
        self.duration_seconds = None
        self.model_name = None
        self.input_tokens = None
        self.output_tokens = None
        self.output_text = None
        self.thinking = None
        self.parsing_error = None

        # tool fields
        self.tool_name = None
        self.argument = None
        self.status = None
        self.result_text = None

        # note field
        self.note_text = None


class RunLog:
    """
    The full record of one agent run, from start() to finish().

    The recording methods also print one compact live line each, so
    the terminal output and the stored log always match.
    """

    def __init__(self):
        self.invoice_name = None
        self.started_at = None
        self.finished_at = None
        self.steps = []
        self.problem_lines = []
        self.value_lines = []  # the row agent's lookup values, shown instead of problems
        self.resolution_lines = []
        self.lookups_used = 0
        self.finds_values = False

        # live terminal lines can be switched off for batch runs;
        # everything is still recorded and can be printed or saved after
        self.live_output = True

    def start(self, invoice_name: str) -> None:
        """
        Begin a new run: reset everything and print the start banner.

        Args:
            invoice_name (str): Display name of the invoice file.
        """

        self.invoice_name = invoice_name
        self.started_at = datetime.now()
        self.finished_at = None
        self.steps = []
        self.problem_lines = []
        self.value_lines = []  # the row agent's lookup values, shown instead of problems
        self.resolution_lines = []
        self.lookups_used = 0
        self.finds_values = False
        if self.live_output:
            print(f"\n===== RUN START: {invoice_name} =====")

    def note(self, text: str) -> None:
        """
        Record a short flow note (stage changes, loop stops) and print it.

        Args:
            text (str): The note to record.
        """

        step = StepLog("note", "note")
        step.note_text = text
        self.steps.append(step)
        if self.live_output:
            print(f"\n{text}")

    def log_llm_step(self, step_name: str, duration_seconds: float, model_name,
                     input_tokens, output_tokens, output_text, thinking,
                     parsing_error) -> None:
        """
        Record one LLM call and print its live stats line.

        Args:
            step_name (str): Which node called, e.g. "identify".
            duration_seconds (float): Wall time of the call.
            model_name (str | None): Model that answered.
            input_tokens (int | None): Prompt tokens used.
            output_tokens (int | None): Generated tokens.
            output_text (str | None): The parsed output as JSON text,
                without the thinking field.
            thinking (str | None): The model's private reasoning. Only
                shown in the full view and saved files.
            parsing_error (str | None): Problem with the generation,
                None when it parsed cleanly.
        """

        step = StepLog("llm", step_name)
        step.duration_seconds = duration_seconds
        step.model_name = model_name
        step.input_tokens = input_tokens
        step.output_tokens = output_tokens
        step.output_text = output_text
        step.thinking = thinking
        step.parsing_error = parsing_error
        self.steps.append(step)

        if self.live_output:
            print(f"[{step_name}] {duration_seconds:.1f}s, tokens in/out: {input_tokens}/{output_tokens}")
            if parsing_error is not None:
                print(f"[{step_name}] problem: {parsing_error}")

    def log_tool_step(self, tool_name: str, argument: str, status: str, result_text: str) -> None:
        """
        Record one tool call and print its live line.

        The argument and result can hold person names, so the live
        line shows only the tool name and status.

        Args:
            tool_name (str): The tool that ran, e.g. "lookup_person".
            argument (str): The value that was looked up.
            status (str): Short outcome, e.g. "found" or "exact".
            result_text (str): The full tool result as JSON text.
        """

        step = StepLog("tool", "tool")
        step.tool_name = tool_name
        step.argument = argument
        step.status = status
        step.result_text = result_text
        self.steps.append(step)
        self.lookups_used = self.lookups_used + 1

        if self.live_output:
            print(f"[tool] {tool_name} -> {status}")

    def finish(self, final_state: dict) -> None:
        """
        Close the run and store the outcome from the final graph state.

        Args:
            final_state (dict): The state dict agent.invoke() returned.
        """

        self.finished_at = datetime.now()

        labeled = final_state.get("labeled_problems")
        if labeled is not None:
            for problem in labeled.problems:
                self.problem_lines.extend(_wrap(problem.problem, f"[Class {problem.label}] ", "    "))
                self.problem_lines.append(f"    field: {problem.field} | value: {problem.value}")
                self.problem_lines.append("")

        resolutions = final_state.get("resolutions")
        if resolutions is not None:
            for resolution in resolutions:
                if resolution.resolved:
                    status = "resolved"
                else:
                    status = "unresolved"
                self.resolution_lines.extend(_wrap(resolution.problem, f"[{status}] ", "    "))
                self.resolution_lines.extend(_wrap(resolution.suggestion, "    suggestion: ", "                "))
                self.resolution_lines.extend(_wrap(resolution.source, "    source:     ", "                "))
                self.resolution_lines.extend(_wrap(resolution.why, "    why:        ", "                "))
                self.resolution_lines.append("")

        # the row agent gives one answer for the row instead of one resolution per problem
        # the row agent finds lookup values instead of problems
        self.finds_values = "values" in final_state
        found = final_state.get("values")
        if found is not None:
            for lookup_value in found.lookup_values:
                self.value_lines.append(f"{lookup_value.kind}: {lookup_value.value} | field: {lookup_value.field}")

        answer = final_state.get("answer")
        if answer is not None:
            if answer.resolved:
                status = "resolved"
            else:
                status = "unresolved"
            self.resolution_lines.extend(_wrap(", ".join(answer.koststeder), f"[{status}] koststeder: ", "    "))
            self.resolution_lines.extend(_wrap(answer.source, "    source:     ", "                "))
            self.resolution_lines.extend(_wrap(answer.why, "    why:        ", "                "))
            self.resolution_lines.append("")

        if self.live_output:
            print(f"\n===== RUN FINISHED in {self.duration_seconds():.1f}s =====")

    def duration_seconds(self) -> float:
        """
        Total run time in seconds, 0.0 while the run is not finished.

        Returns:
            float: Seconds between start() and finish().
        """

        if self.started_at is None or self.finished_at is None:
            return 0.0
        delta = self.finished_at - self.started_at
        return delta.total_seconds()

    def total_input_tokens(self) -> int:
        """
        Sum of prompt tokens over all LLM steps.

        Returns:
            int: Total input tokens.
        """

        total = 0
        for step in self.steps:
            if step.kind == "llm" and step.input_tokens is not None:
                total = total + step.input_tokens
        return total

    def total_output_tokens(self) -> int:
        """
        Sum of generated tokens over all LLM steps.

        Returns:
            int: Total output tokens.
        """

        total = 0
        for step in self.steps:
            if step.kind == "llm" and step.output_tokens is not None:
                total = total + step.output_tokens
        return total

    def model_used(self):
        """
        The model that answered in this run.

        Returns:
            str | None: The model name of the first LLM step that reported one, else None.
        """

        for step in self.steps:
            if step.kind == "llm" and step.model_name is not None:
                return step.model_name
        return None

    def _summary_lines(self) -> list:
        """
        Build the summary view: the outcome of the run in a few lines.

        Returns:
            list[str]: The summary, one line per entry.
        """

        lines = []
        lines.append(f"===== SUMMARY: {self.invoice_name} =====")
        lines.append(
            f"duration: {self.duration_seconds():.1f}s"
            f" | llm tokens in/out: {self.total_input_tokens()}/{self.total_output_tokens()}"
            f" | lookups: {self.lookups_used}")

        lines.append("")
        if self.finds_values:
            lines.append("Lookup values:")
            if len(self.value_lines) == 0:
                lines.append("    (none)")
            for line in self.value_lines:
                lines.append(f"    {line}")
            lines.append("")
        else:
            lines.append("Problems:")
            if len(self.problem_lines) == 0:
                lines.append("    (none)")
            for line in self.problem_lines:
                lines.append(f"    {line}")

        lines.append("Resolutions:")
        if len(self.resolution_lines) == 0:
            lines.append("    (none)")
        for line in self.resolution_lines:
            lines.append(f"    {line}")

        return lines

    def _step_lines(self, step: StepLog) -> list:
        """
        Build the medium-detail lines for one recorded step (no thinking).

        Args:
            step (StepLog): The step to render.

        Returns:
            list[str]: The rendered lines.
        """

        if step.kind == "note":
            return [f"[note] {step.note_text}"]

        if step.kind == "tool":
            lines = []
            lines.append(f"[tool] {step.tool_name}")
            lines.append(f"    argument: {step.argument}")
            lines.append(f"    status:   {step.status}")
            lines.append("    result:")
            for result_line in str(step.result_text).split("\n"):
                lines.append(f"        {result_line}")
            return lines

        # llm step
        lines = []
        lines.append(
            f"[{step.step_name}] {step.duration_seconds:.1f}s"
            f", tokens in/out: {step.input_tokens}/{step.output_tokens}"
            f", model: {step.model_name}")
        if step.parsing_error is not None:
            lines.append(f"    problem: {step.parsing_error}")
        if step.output_text is not None:
            lines.append("    output:")
            for output_line in step.output_text.split("\n"):
                lines.append(f"        {output_line}")
        return lines

    def _full_lines(self) -> list:
        """
        Build the complete log: summary plus every step, with thinking.

        Returns:
            list[str]: The full log, one line per entry.
        """

        lines = self._summary_lines()
        lines.append("")
        lines.append(f"===== FULL LOG ({len(self.steps)} steps) =====")

        step_number = 0
        for step in self.steps:
            step_number = step_number + 1
            lines.append("")
            lines.append(f"--- step {step_number} ---")
            lines.extend(self._step_lines(step))

            if step.kind == "llm" and step.thinking is not None:
                lines.append("    thinking:")
                for thinking_line in step.thinking.split("\n"):
                    lines.extend(_wrap(thinking_line, "        ", "        "))

        return lines

    def print_summary(self) -> None:
        """
        Print the short outcome view of the run.
        """

        print("\n".join(self._summary_lines()))

    def print_step(self, step_name: str) -> None:
        """
        Print every recorded step with the given name, medium detail.

        Args:
            step_name (str): "identify", "classify", "resolve", "tool"
                or "note".
        """

        lines = []
        for step in self.steps:
            if step.step_name == step_name:
                lines.extend(self._step_lines(step))

        if len(lines) == 0:
            lines.append(f"(no steps recorded for '{step_name}')")

        print("\n".join(lines))

    def print_full(self) -> None:
        """
        Print the complete log, including the models' thinking.
        """

        print("\n".join(self._full_lines()))

    def save_txt(self, path=None):
        """
        Save the complete log as a text file.

        Args:
            path (str | Path | None): Target file. Default is
                run_<invoice>_<timestamp>.txt in LOGS_DIR.

        Returns:
            Path: The file that was written.
        """

        if path is None:
            logs_dir = LOGS_DIR
            logs_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^A-Za-z0-9-]+", "_", str(self.invoice_name))
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = logs_dir / f"run_{safe_name}_{timestamp}.txt"

        output_path = Path(path)
        output_text = "\n".join(self._full_lines())
        with open(output_path, "w", encoding="utf-8") as log_file:
            log_file.write(output_text + "\n")

        if self.live_output:
            print(f"Log saved to {output_path}")
        return output_path


if __name__ == "__main__":
    # invented stand-ins for the schema objects - never real data
    class _FakeProblem:
        def __init__(self):
            self.label = 1
            self.problem = "buyer_reference is a person name"
            self.field = "buyer_reference"
            self.value = "Nordmann, Ola"

    class _FakeLabeled:
        def __init__(self):
            self.problems = [_FakeProblem()]

    class _FakeResolution:
        def __init__(self):
            self.resolved = True
            self.problem = "buyer_reference is a person name"
            self.suggestion = "32140000"
            self.source = "lookup_person exact match"
            self.why = "the person maps to exactly one koststed"

    log = RunLog()
    log.start("Faktura 0000")
    log.note("Entering Classification Stage")
    log.log_llm_step("identify", 1.5, "test-model", 100, 50, '{"problems": []}', "invented thinking", None)
    log.log_tool_step("lookup_person", "Nordmann, Ola", "exact", '{"match_type": "exact"}')
    log.finish({"labeled_problems": _FakeLabeled(), "resolutions": [_FakeResolution()]})

    summary_text = "\n".join(log._summary_lines())
    assert "Faktura 0000" in summary_text
    assert "[Class 1]" in summary_text
    assert "lookups: 1" in summary_text
    assert "invented thinking" not in summary_text

    full_text = "\n".join(log._full_lines())
    assert "invented thinking" in full_text
    assert "lookup_person" in full_text

    assert log.total_input_tokens() == 100
    assert log.total_output_tokens() == 50

    print("\nrun_log self-check passed")
