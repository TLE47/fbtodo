#!/usr/bin/env bash
# Report a failed suite step so a red job says WHAT broke, not just "exit 1".
#
# The tail goes two places on purpose:
#   * the job summary, for the human reading the run in the browser;
#   * error annotations, because those are readable over the REST API WITHOUT a token —
#     which is the only way to debug a failing run from a machine with no `gh` and no
#     stored credentials.
#
# Usage: ci-report.sh <step-name> <log-file> <exit-code>   (does nothing when rc is 0)
set -u

name="${1:-step}"
log="${2:-}"
rc="${3:-1}"

if [ "$rc" = "0" ]; then
  exit 0
fi

tail_text="$(tail -n 80 "$log" 2>/dev/null || true)"

{
  echo "### ${name} failed — last 80 lines"
  echo '```'
  printf '%s\n' "$tail_text"
  echo '```'
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"

# Both ends are worth having and neither replaces the other:
#   * the FIRST FAIL lines name what broke — a suite that fails dozens of checks ends its
#     tail in PASSes, so the tail alone says nothing; a cascade's cause is at its start, and
#     the first FAIL is where the whole log's own failures begin;
#   * the TAIL is where a capped run stopped, which is the only clue a hang leaves behind.
annotate="$tail_text"
if grep -q '^FAIL' "$log" 2>/dev/null; then
  annotate="$(grep '^FAIL' "$log" | head -n 4)
$(tail -n 6 "$log")"
fi

# One annotation per line, newest last. Workflow commands are one line each, so newlines are
# not a problem; `%` and CR do need escaping.
printf '%s\n' "$annotate" | tail -n 10 | while IFS= read -r line; do
  esc="$(printf '%s' "$line" | sed -e 's/%/%25/g' -e 's/\r/%0D/g')"
  echo "::error title=${name}::${esc}"
done

exit 0
