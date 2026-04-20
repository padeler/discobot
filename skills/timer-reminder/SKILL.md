---
name: timer-reminder
description: >
  Use when the user asks to set a reminder, create a timer, schedule a notification,
  or wake them up later. Also use for listing, canceling, or managing pending reminders.
  Keywords: remind, timer, alert, notification, wake, later, when, cancel reminder, list reminders, schedule, timer.
version: 1.0.0
---

## Instructions

### Setting reminders
1. Parse the user's natural language request to extract:
   - **Delay** (e.g., "in 5 minutes", "after 30 mins", "in 2 hours")
   - **Specific time** (e.g., "at 5pm", "at 14:00", "tomorrow at noon")
   - **Message** (what to remind about)
2. Support these formats:
   - "remind me [message] in [X time]" (e.g., "remind me to drink water in 15 minutes")
   - "[message] in [X time]" (e.g., "stop cooking in 10 minutes")
   - "wake me up at [time]" (e.g., "wake me up at 7am")
   - "remind me [message] at [time]" (e.g., "remind me to call mom at 6pm")
3. If no time is given, ask the user for a duration.
4. Confirm the reminder with the user: "Added reminder: [message] in [X minutes/hours]"

### Managing reminders
- Use the `list_reminders` tool to get pending reminders
- Use the `cancel_reminder` tool with the reminder ID to cancel
- If the user says "cancel a reminder" without details, show pending reminders

### Output format
- Confirmation: "Reminder set: [message] — [time until]"
- List: Show numbered list with ID, message, and time until
- Cancel: "Cancelled reminder #N: [message]"
