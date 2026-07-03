# Key management — Implementation Review

> For the reviewer. Created before implementation; fill in after reviewing the completed work.
> Implementers follow `-plan.md` only — do not read this file unless the user asks.

## Review notes

<!-- Corrections, gaps, and follow-ups discovered while reviewing the implementation. -->

## Items to address

- [x] Look at the requirements for documenting code and functions in AGENTS.md. Make sure this is implemented for all the code we have written
- [x] The key fields currently show passowrd manager icons in them.. Can we avoid that? They should behave as password fields, but we don't want the password manager to latch on to them
- [x] "Local OpenAI" is not a good provider name. Also, this provider currently does not require a key. We should not show a key entry for it..
- [x] Let's get rid of default user credentials.. They make things more complicated. Let's only have named credentials that the user can add. i.e. the credentialRole can go away then.. since for SystemCredential, they are ONLY default.. Right?
- [x] instead of the workaround for fallback CHIEF_CREDENTIALS_KEY in @crypto.py, let's just provide a default in our settingsbase.. also, call it CREDENTIALS_KEY. Also, create .env.production and write a note there (not a comment.. intent is to break compilation), that CREDENTIALS_KEY must be set before starting on production deployments
- [x] Explain and discuss why we need to have both SYstemCredential and UserCredential.. don't get rid of yet, but discuss it with me
