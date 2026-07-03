

USECASES:
- U1: Clean up my inbox
  - ME: archive old tags I no longer need
  - Goal: Minimize time I spend shuffling emails
    - Tag every email with what action was taken x-action, x-read, x-spam, etc
    - Things I need to do, separately from email -> Create a clickup task for it, in a clickup INBOX
    - Things I need to read, informationally, but not time sensitive, tag #x-read and elevate importance
    - Things I need to act on in the inbox, tag #x-act, and elevate importance more
    - Spam, mark x-spam and move to spam
    - Not important, mark x-unimp (and have gmail organize it)
    - Notes from myself
      - Some could go do my obsidian inbox, with dates for the log entries
      - Some go to clickup, e.g. floors or other projects, but always to an INBOX so we don't mix it up with other things
  - Components
    - Gmail
        - Investigate API vs MCP. I likely want to use a key instead of oauth, so likely API
    - Clickup
        - Investigate API vs MCP


- U2: Reading list
  - Goal: Find longer form content I should read, related to competitors, technology, management, LLMs, NEWS, etc.
  - Sources:
    - Blogs:
        - Last week in AI
        - Hacker news
        - The labs: OpenAI, Google, etc
  - Format:
    - Initially a list of news articles I can click to each of them
  - Long term
    - Dedupe information. Don't show things I have already seen
    - Do relevance matching, and allow me to provide feedback
    - Automatically discover and add to the list of sources
    - Keep an inventory of articles we have seen and evaluated, and track which I read and which I liked

- U3: Home Assistant automation
  -











USABILITY
- [ ] for both console mode and html mode, we want to render markdown output properly. We also want to support latex formulas if they are present. Make this a clean implementation.
