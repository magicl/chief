# Chief — Usability

Status: **done**

SPEC:

- [x] **New agent → agent screen**: When clicking a new-agent button on the Chief page, go directly into that agent (no intermediate stop).

- [x] **Agent identifier is a link**: Clicking an agent's identifier opens the agent screen.

- [x] **Agent screen layout**: Two sections — a dashboard (most of the screen) and a chatbox at the bottom.
  - Dashboard shows previous sessions for that agent.
  - Chatbox has a text input; type a message and press Enter to start a new chat and navigate to the session view.

- [x] **Seamless agent → session transition**: Starting a chat from the agent screen should feel continuous — the chatbox stays in place and looks unchanged; only the top of the screen switches from the dashboard to the conversation view (messages back and forth).

- [x] **Shared chatbox component**: The bottom chatbox is one shared component used by both the agent screen and the session (conversation) view.

- [x] **Fixed outer frame, scrollable inner panels**: The overall view (agent screen and session view) must not scroll — the chatbox is always visible at the bottom. Only the dashboard and dialog (conversation) sections scroll when content overflows.

- [x] **Dialog auto-scroll with manual override**: The conversation view scrolls to the most recent message as new messages arrive, unless the user has scrolled manually (e.g. by dragging the scrollbar). Provide a **Follow** control that toggles automatic follow mode on or off (in addition to manual scrollbar interaction).

- [x] **Session identifier is a link**: Clicking a session's identifier opens that session (conversation view).
