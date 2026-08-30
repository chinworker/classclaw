# Web-only class creation

OpenClaw agents do not create classes and the plugin intentionally exposes no onboarding mutation tools.

When a user asks to create a class:

1. Ask them to open the ClassClaw web app at `/app/` and connect OpenClaw first.
2. The user enters deterministic class basics in named fields.
3. Student roster and timetable are uploaded as files. Each upload is parsed in a fresh, extraction-only OpenClaw session and replaces the prior target preview.
4. The user edits the structured preview table directly. Valid edits are saved without another model pass.
5. Subjects are derived from the current timetable. There is no onboarding subject-score setting, and blank rooms default to the class room.
6. The web app produces a final server-validated proposal. Only explicit web confirmation creates the class.
7. After creation, the app provisions an isolated OpenClaw agent, starts WeChat QR login, displays the QR code, and binds that channel account to the new agent.

If any provisioning step fails, the class remains valid and the web app displays a retry action. Never work around this by creating an agent or binding from conversation.
