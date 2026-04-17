# Discord Bot Skills System Design

**Date:** 2026-04-17  
**Author:** Mech Knight Bot Team

## Overview

Add a skills system to the Discord bot that allows it to perform utility functions (reminders, polls, notes) through natural conversation, without requiring explicit commands.

## Goals

1. **Natural interaction** - Users express intent naturally, bot understands via LLM
2. **Extensible** - New skills added via YAML config, no code changes
3. **Context-aware** - Bot responds in same channel/DM where request originated
4. **Consistent storage** - Uses JSON files matching existing history system

## Architecture

### Components

1. **Skills Config (`skills.yaml`)**
   - Defines available skills with name, description, and examples
   - Single source of truth for what the bot can do

2. **Intent Detector**
   - LLM-based detection using a unified prompt
   - Checks every message against available skills
   - Returns skill name + extracted parameters or "none"

3. **Skill Executor**
   - Routes detected intents to skill handlers
   - Extracts parameters from LLM response
   - Handles ambiguous cases by asking for clarification

4. **Response Handler**
   - Acknowledges skill execution to user
   - Replies in same context (channel/DM) as the request
   - For scheduled notifications (reminders), posts in original context with user mention

5. **Data Storage**
   - JSON files in `data/` directory
   - One file per skill: `reminders.json`, `notes.json`, `polls.json`
   - Follows existing history storage pattern

### Data Flow

```
User Message → Intent Detection (LLM) → Skill Match?
                                          ↓ Yes
                              Extract Parameters → Execute Skill → Acknowledge
                                          ↓
                              Store in JSON → Schedule/Perform Action
```

## Skills Configuration Format

```yaml
skills:
  - name: reminder
    description: Set reminders for tasks or events
    examples:
      - "remind me in 5 minutes to check the oven"
      - "don't forget to call John at 3pm"
      - "notify me tomorrow about the meeting"
    
  - name: poll
    description: Create quick polls for group decisions
    examples:
      - "where should we eat"
      - "vote for movie night"
      - "what time works for everyone"
    
  - name: note
    description: Save and retrieve personal notes
    examples:
      - "remember this: buy milk"
      - "note: John's phone number is 555-1234"
      - "save this for later"

  - name: decision
    description: Help make decisions between options
    examples:
      - "should I order pizza or burgers"
      - "help me decide"
      - "what do you think about"
```

## Intent Detection Prompt

The LLM receives a prompt structured as:

```
You are an intent classifier for a Discord bot. Available skills:

{skills_list_with_descriptions}

For the following message, determine:
1. Does it match any skill? (respond "none" if not)
2. If yes, which skill and what parameters?

Message: "{user_message}"

Respond in JSON format:
{{"skill": "skill_name", "params": {{...}}}} or {{"skill": "none"}}

If ambiguous (could be multiple skills), respond:
{{"skill": "ambiguous", "candidates": ["skill1", "skill2"], "question": "clarification question"}}
```

## Data Schemas

### reminders.json
```json
{
  "user_id": "123456789",
  "channel_id": "987654321",
  "guild_id": "111222333",
  "message": "check the oven",
  "scheduled_time": "2026-04-17T15:30:00",
  "created_at": "2026-04-17T15:25:00",
  "notified": false
}
```

### notes.json
```json
{
  "entries": [
    {
      "user_id": "123456789",
      "content": "buy milk",
      "created_at": "2026-04-17T15:25:00"
    }
  ]
}
```

### polls.json
```json
{
  "poll_id": "unique_id",
  "channel_id": "987654321",
  "message_id": "discord_message_id",
  "question": "Where should we eat?",
  "options": ["Pizza", "Burgers", "Sushi"],
  "votes": {
    "user1": "Pizza",
    "user2": "Burgers"
  },
  "created_by": "123456789",
  "created_at": "2026-04-17T15:25:00",
  "active": true
}
```

## Skill Implementations

### Reminder Skill
- **Parameters:** `delay` (e.g., "5 minutes"), `task` (what to remind)
- **Actions:** 
  - Parse natural language time expressions
  - Store reminder in `data/reminders.json`
  - Schedule async check (existing message_processor loop)
  - Post notification when due with user mention
- **Commands (fallback):** `/reminders list`, `/reminders cancel`

### Poll Skill
- **Parameters:** `question`, `options` (optional, can be generated)
- **Actions:**
  - Create interactive poll message with reactions
  - Track votes in `data/polls.json`
  - Allow closing poll via reaction or command
  - Announce results when closed

### Note Skill
- **Parameters:** `content` (the note text)
- **Actions:**
  - Save note with user ID and timestamp
  - Support retrieval: "show my notes", "what did I note yesterday"
  - Notes are user-specific

### Decision Skill
- **Parameters:** `options` (choices to decide between)
- **Actions:**
  - Use LLM to provide reasoning for each option
  - Make a recommendation
  - Can be fun/silly for trivial decisions

## Response Behavior

1. **Acknowledge execution:** "Got it! I'll remind you in 5 minutes."
2. **Same context:** Reply in same channel/DM where request came from
3. **Scheduled notifications:** Post in original channel with user mention
4. **Ambiguity:** Ask clarification question before proceeding

## Error Handling

1. **LLM detection fails:** Log error, treat as "none" (normal conversation)
2. **Skill execution fails:** Inform user, log details
3. **Missing parameters:** Ask user for missing info
4. **JSON file errors:** Log, create fresh file, continue

## Integration with Existing Bot

1. **Message flow:** Intent check happens before normal response logic
2. **If skill detected:** Execute skill AND respond conversationally
3. **If no skill:** Normal bot behavior (auto-response or mention-based)
4. **No changes to:** History system, Ollama integration, config loading

## Future Extensions

- More skills (timers, shared lists, weather, etc.)
- Skill permissions (admin-only skills)
- Skill scheduling (recurring reminders)
- Cross-server skill data sharing

## Implementation Notes

- Keep `skills.yaml` hot-reloadable (check on each message or periodic)
- Use asyncio locks for JSON file access
- Reminder checker integrates into existing `message_processor` loop
- LLM prompt caching for intent detection (same prompt, different messages)
