# Pulse WebSocket API v1
Port: `7550`

## Outbound Events (Core -> UI)
Core sends these JSON events to any connected UI clients.

### `state`
Indicates the current state of the assistant.
```json
{
  "v": 1,
  "type": "state",
  "payload": "idle|listening|thinking|acting|speaking"
}
```

### `transcript`
Live transcription of what the user is saying or what the assistant understood.
```json
{
  "v": 1,
  "type": "transcript",
  "payload": "Open notepad"
}
```

### `action`
Indicates what tool the assistant decided to run.
```json
{
  "v": 1,
  "type": "action",
  "tool": "open_app",
  "params": {"name": "notepad.exe"}
}
```

### `feedback`
Spoken feedback or textual response.
```json
{
  "v": 1,
  "type": "feedback",
  "text": "Opening Notepad for you.",
  "mode": "Standard"
}
```

### `error`
Any system or execution error.
```json
{
  "v": 1,
  "type": "error",
  "message": "Failed to open app."
}
```

## Inbound Events (UI -> Core)
UI sends these JSON events to command the core.

### `text_command`
Simulates a voice command via text.
```json
{
  "v": 1,
  "type": "text_command",
  "text": "Close chrome"
}
```

### `cancel`
Interrupts the current operation (like saying "Pulse").
```json
{
  "v": 1,
  "type": "cancel"
}
```

### `set_config`
Updates config settings dynamically.
```json
{
  "v": 1,
  "type": "set_config",
  "key": "feedback_mode",
  "value": "Minimal"
}
```
