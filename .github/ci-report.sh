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

# A suite that fails dozens of checks ends its tail in PASSes, so the tail alone hides WHAT
# broke. When the log has FAIL lines, annotate those (first ones) instead: they name the
# checks, and the tail of them is where a cascade from one cause starts.
annotate="$tail_text"
grep -q '^FAIL' "$log" 2>/dev/null && annotate="$(grep '^FAIL' "$log" | head -n 6)"

# One annotation per line, newest last. Workflow commands are one line each, so newlines are
# not a problem; `%` and CR do need escaping.
printf '%s\n' "$annotate" | tail -n 6 | while IFS= read -r line; do
  esc="$(printf '%s' "$line" | sed -e 's/%/%25/g' -e 's/\r/%0D/g')"
  echo "::error title=${name}::${esc}"
done

exit 0
