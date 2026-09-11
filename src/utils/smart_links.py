"""
Smart Links for Amaze on Work.

Generates clickable VS Code deep-links and formatted code snippets
for every fix location in reports, Slack messages, and Jira comments.

Ported and extended from the tata-lcr project (src/utils/smart_links.py).
"""

import os
from pathlib import Path


class SmartLinks:
    """
    Generates VS Code deep-links and code snippet fragments
    for any file + line reference.
    """

    def vscode_link(
        self,
        file_path: str,
        line: int,
        end_line: int | None = None,
    ) -> str:
        """
        Return a vscode:// deep-link URL.

        Format: vscode://file/{abs_path}:{line}:1:{end_line}:999
        Clicking opens the file and highlights the line range.
        """
        abs_path = os.path.abspath(file_path).replace("\\", "/")
        if end_line and end_line != line:
            return f"vscode://file/{abs_path}:{line}:1:{end_line}:999"
        return f"vscode://file/{abs_path}:{line}:1:{line}:999"

    def format_snippet(
        self,
        file_lines: list[str],
        highlight_line: int,
        context: int = 3,
        start_line_offset: int = 1,
    ) -> str:
        """
        Return a plain-text code snippet with line numbers.

        Args:
            file_lines: All lines of the file (zero-indexed list).
            highlight_line: The 1-indexed line to highlight (the bug/fix line).
            context: Number of lines before/after to include.
            start_line_offset: Line number that file_lines[0] corresponds to.

        Returns:
            Multi-line string like:
                45 │ def authenticate(user, password):
                46 │     if user is None:
             >> 47 │         return None        ← highlight
                48 │     ...
        """
        idx = highlight_line - start_line_offset  # 0-indexed position in file_lines
        start_idx = max(0, idx - context)
        end_idx = min(len(file_lines) - 1, idx + context)

        output_lines = []
        for i in range(start_idx, end_idx + 1):
            lineno = start_line_offset + i
            raw_line = file_lines[i].rstrip()
            marker = ">>" if lineno == highlight_line else "  "
            output_lines.append(f"{marker} {lineno:4} | {raw_line}")

        return "\n".join(output_lines)

    def slack_file_ref(self, file_path: str, line: int, end_line: int | None = None) -> str:
        """
        Return a Slack-formatted file reference.
        Slack doesn't support vscode:// links natively, so we display
        a readable path:line with the link in angle-bracket format.

        e.g.:  `src/auth/user.py:47` (<vscode://file/...>)
        """
        rel = self._relative(file_path)
        suffix = f":{end_line}" if end_line and end_line != line else ""
        link = self.vscode_link(file_path, line, end_line)
        return f"`{rel}:{line}{suffix}` (<{link}|Open in VS Code>)"

    def jira_file_ref(self, file_path: str, line: int, end_line: int | None = None) -> str:
        """
        Return a Jira-wiki-formatted file reference.
        Jira supports {{code}} blocks and [text|url] links.

        e.g.:  [src/auth/user.py:47|vscode://file/...]
        """
        rel = self._relative(file_path)
        suffix = f":{end_line}" if end_line and end_line != line else ""
        link = self.vscode_link(file_path, line, end_line)
        return f"[{rel}:{line}{suffix}|{link}]"

    def format_before_after(
        self,
        file_path: str,
        before_lines: list[str],
        after_lines: list[str],
        changed_line: int,
    ) -> str:
        """
        Format a before/after diff snippet for reports and Jira comments.

        Returns plain-text:
            Fix applied to: src/auth/user.py:L47
            [Open in VS Code](vscode://...)

            Before:
            >> 47 | return bcrypt.check_password_hash(user.password_hash, password)

            After:
            >> 47 | if not user.password_hash:
               48 |     return False
               49 | return bcrypt.check_password_hash(user.password_hash, password)
        """
        rel = self._relative(file_path)
        link = self.vscode_link(file_path, changed_line)

        before_snippet = self.format_snippet(before_lines, changed_line)
        after_snippet = self.format_snippet(after_lines, changed_line)

        return (
            f"Fix applied to: {rel}:L{changed_line}\n"
            f"[Open in VS Code]({link})\n\n"
            f"Before:\n{before_snippet}\n\n"
            f"After:\n{after_snippet}"
        )

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _relative(file_path: str) -> str:
        """Return relative path from cwd for cleaner display."""
        try:
            return str(Path(file_path).relative_to(Path.cwd()))
        except ValueError:
            return file_path


# Module-level singleton
smart_links = SmartLinks()
