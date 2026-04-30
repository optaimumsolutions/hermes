#!/bin/bash
# Hermes cron jobs for outreach pipeline orchestration
# Run this after deploying to Render to set up the automation loop

RENDER_URL="${OUTREACH_API_URL:-https://outreach-system.onrender.com}"

echo "Setting up Hermes cron jobs for outreach system..."
echo "API URL: $RENDER_URL"

# Daily report at 9am - pipeline stats to Telegram
cat > ~/.hermes/cron/daily_report.yaml << EOF
name: "Outreach Daily Report"
schedule: "0 9 * * *"
command: "curl -s -X POST $RENDER_URL/pipeline/daily-report"
notify: telegram
EOF

# Scout: discover leads every 4 hours during business hours
cat > ~/.hermes/cron/scout_discover.yaml << EOF
name: "Scout Lead Discovery"
schedule: "0 8,12,16 * * 1-5"
prompt: |
  Check the outreach system pipeline stats at $RENDER_URL/pipeline/stats.
  If qualified leads are below 20, use the Scout to discover new leads.
  Focus on SaaS companies with 10-200 employees.
  Report findings to Telegram.
notify: telegram
EOF

# Sender: send batch at 10am and 2pm weekdays
cat > ~/.hermes/cron/sender_batch.yaml << EOF
name: "Sender Email Batch"
schedule: "0 10,14 * * 1-5"
prompt: |
  Check qualified leads at $RENDER_URL/scout/qualified.
  If there are leads ready, trigger a send batch at $RENDER_URL/sender/send-batch.
  Report how many emails were queued.
notify: telegram
EOF

# Check for replies every 2 hours
cat > ~/.hermes/cron/check_replies.yaml << EOF
name: "Reply Monitor"
schedule: "0 */2 * * 1-5"
prompt: |
  Check the sender stats at $RENDER_URL/sender/stats.
  If there are new replies, alert me immediately with the lead details.
  For positive replies, draft a response suggestion.
notify: telegram
EOF

echo "Cron jobs created. Verify with: ls ~/.hermes/cron/"
echo ""
echo "To test the pipeline:"
echo "  curl $RENDER_URL/health"
echo "  curl $RENDER_URL/pipeline/stats"
