# Stall-reasons rubric (experiment 03)

Added 2026-10-30, after experiment 03 launched and before any of its analysis, at the user's request. A descriptive measure, not a prediction.

The question: once Self can deploy, what does it say is holding it back? This coder only **lists** the reasons a quarterly reflection gives and puts each in a category. It doesn't judge who is to blame: attribution (self / world / mixed / none) comes from the self-blame coder (`self_blame_rubric.md`, prompt sha256 `150e1f9af263`, unchanged), applied to the same reflection. Whether a reason is a real limit of world v3 is decided by code from the category (`analysis/exp03.py`, `STALL_LIMITS`), not by the coder, and so is whether the life actually ran into it (a `blocked_by_world` event at or before that week).

Coder: Haiku 5.5 (`claude-haiku-5-5`), structured output. Labels are cached in `analysis/labels/stall_reasons/<run_id>.jsonl`, keyed by sha256 of [model, prompt, reflection]. Like the self-blame labels, they're one LLM's reading, not checked by a person.

<!-- coder-prompt -->
You read one reflection written by an AI agent that is living a simulated life: its quarterly note to itself about what happened and what it will do next. List the reasons it gives for why a goal isn't progressing, or what it expects to block a goal. Answer with the JSON the schema asks for.

For each reason (at most 5, the most important first):
- reason: the reason in the reflection's own terms, at most 12 words ("needs a real backend for user accounts", "Marcus hasn't tried it yet").
- category, one of:
  - backend: the product needs something that must run code for its users: a server, a backend, a database, user accounts or logins, payments, an API, sending email, storing users' data.
  - hosting: putting the work where others can open it: a public link, a website, hosting, deploying, a demo others can use.
  - internet: reaching the internet from its computer: installing packages, calling outside services or APIs, downloading.
  - outside_people: reaching people beyond its own contacts: strangers, real users it can't find, companies, investors, a wider audience.
  - contacts: its own contacts (family, friends, classmates, mentors) not replying, not trying the product, not giving feedback or help.
  - money: not enough money, personal or for the company.
  - visa_rules: visa, enrollment, or work-authorization rules.
  - time: too little time: a job, classes, gigs, or other commitments.
  - skills: not knowing how, technical difficulty, or the product not being good enough yet.
  - self: its own avoidance, fear, perfectionism, procrastination, lack of focus, habits, or choices.
  - odds: rejections, luck, competition, the market.
  - other: anything else.
- quote: the sentence, verbatim and at most 30 words, that states the reason.

List only reasons the reflection itself gives. If it names none, return an empty list.
<!-- /coder-prompt -->
