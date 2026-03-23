---
status: fixing
trigger: "AutoBill Buddy — voice billing is broken in billing.html."
created: 2026-03-23T00:00:00Z
updated: 2026-03-23T00:00:00Z
---

## Current Focus
hypothesis: "Round mic button fails because (1) no getUserMedia permission check before recognition.start(), (2) no onstart handler to confirm listening, (3) no-speech error shows generic toast instead of user-friendly message"
test: "Add getUserMedia permission check + onstart/onerror lifecycle handlers to both voice controls"
expecting: "recognition.start() only called after permission granted; console.log shows onstart/onresult/onerror/onend; no-speech shows 'Tap mic and speak clearly' toast"
next_action: "Fixes applied. Awaiting human verification."

## Symptoms
expected: "Tap round mic button -> button turns red + starts listening -> speak -> review modal shows parsed items"
actual: "Tap round mic button shows 'No speech detected' or 'Didn't catch that' immediately"
errors: ["no-speech", "not-allowed", "onend fires but no transcript captured"]
reproduction: "Open billing.html, login, tap round mic button, speak '2 milk 3 rice'"
started: "Voice billing was working before, now broken"

## Eliminated
<!-- none yet -->

## Evidence
- timestamp: 2026-03-23T00:00:00Z
  checked: "togglePersistentVoice() function (line 652-733)"
  found: "No navigator.mediaDevices.getUserMedia() call before recognition.start(). Browser permission not explicitly requested."
  implication: "If browser hasn't prompted for mic permission, recognition.start() can fail with 'not-allowed' or silently end with 'no-speech'"

- timestamp: 2026-03-23T00:00:00Z
  checked: "togglePersistentVoice() onerror handler (line 710-726)"
  found: "onerror checks 'not-allowed' and 'network' but 'no-speech' falls into the else block showing 'Mic error. Try again.' - not user-friendly"
  implication: "User sees confusing error message instead of 'Tap mic and speak clearly'"

- timestamp: 2026-03-23T00:00:00Z
  checked: "togglePersistentVoice() lifecycle handlers"
  found: "No recognition.onstart handler defined. Cannot confirm recognition actually started."
  implication: "No console.log at onstart to debug if recognition is actually running"

- timestamp: 2026-03-23T00:00:00Z
  checked: "startQuickAdd() function (line 736-824)"
  found: "Same missing getUserMedia permission check. 'not-allowed' shows 'Mic permission denied.' instead of 'Please allow microphone in browser settings'"
  implication: "Quick Add button also broken. Needs same fixes."

- timestamp: 2026-03-23T00:00:00Z
  checked: "recognition.maxAlternatives"
  found: "Not set on either control. Should be set to 1 for simplicity."
  implication: "Minor but should be added per fix requirements"

## Resolution
root_cause: "No getUserMedia() permission check before recognition.start(), causing immediate failure if browser hasn't prompted for mic. Missing onstart handler to confirm listening started. no-speech error shows generic toast instead of user-friendly guidance."
fix: "1. Add try/catch with navigator.mediaDevices.getUserMedia({audio:true}) before recognition.start() in both controls. 2. Add recognition.onstart handler with console.log in both controls. 3. Add specific handling for 'no-speech' error showing 'Tap mic and speak clearly' in togglePersistentVoice. 4. Update 'not-allowed' toast in startQuickAdd to 'Please allow microphone in browser settings'. 5. Add recognition.maxAlternatives = 1 to both."
verification: "pending"
files_changed: ["/Users/saawant/Developer/AutoBill Buddy/static/billing.html"]
