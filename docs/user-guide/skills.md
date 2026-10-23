---
title: Using skills
summary: Switch between skills, see how one is set up, make your own copy, and create a new skill in Skill Studio.
audience: user
order: 40
surfaces:
  - frontend/src/components/navigation/SkillSwitcher.tsx
  - frontend/src/components/navigation/SkillsBar.tsx
  - frontend/src/app/skills/studio/
  - frontend/src/components/studio/
reviewed: 2026-10-16
---

A skill is an assistant set up for one kind of job: its instructions, the language model it uses, the tools it may call (web search, your documents, knowledge bases…) and who may use it. The skill you're chatting with is named at the top of the page.

## Switch to another skill

1. Click the skill's name at the top of the page, next to **New chat**.
2. Type in **Search skills…** to narrow the list, or scroll it.
3. Click a skill to chat with it.

Skills are grouped: **Your skills** are the ones you made or copied; the rest are grouped as **Specialists**, **Assistants** and **Tools**, with anything uncategorised under **Other skills**. The list only shows skills you're allowed to use.

![The skill switcher open under Demo Researcher, with a Search skills box and groups for Specialists, Assistants, Tools and Your skills.](img/skills/01-skill-switcher.png "The skill switcher.")

## See how a skill is set up

1. Click **Configure this skill**: the sliders button at the top right. On a phone it's in the menu.
2. Skill Studio opens with the skill's instructions, model, tools and who can use it.

The **Create a new skill** button next to it starts an empty skill instead.

![The two buttons at the top right of the page: Configure this skill (sliders) and Create a new skill (plus).](img/skills/02-header-buttons.png "Configure this skill, and Create a new skill.")

## Built-in skills

Some skills come with {{appName}} — they're kept up to date for everyone and marked as a **platform skill** in Skill Studio. You can open one to see how it's set up, but you can't change it (only a platform admin can). To adapt one, make your own copy.

![Skill Studio on Demo Researcher with a note: This is a built-in platform skill. It's read-only unless you're a platform admin — use Fork to customize to make your own editable copy. A Fork to customize button sits between Cancel and Save.](img/skills/03-built-in-skill.png "A built-in skill: read-only, with Fork to customize.")

## Make your own copy of a skill

1. Open the skill with **Configure this skill**.
2. Click **Fork to customize**.
3. Your copy opens in Skill Studio, named "… (Fork)": same instructions and tools, private to you. Change what you like and click **Save**.

The button is there straight away for any skill you can't change: a built-in skill, or one a colleague shared with you. For a shared skill, Skill Studio also says so at the top and **Save** stays off.

![Skill Studio on Contract Expert, a colleague's skill, with a note: This skill was shared with you, so you can use it but not change it. Fork to customize is the main button and Save is greyed out.](img/skills/09-shared-skill.png "A skill shared with you: fork it to change it.")

> [!WARNING]
> **Your copy is a snapshot.** It won't pick up later improvements to the original. If something should change for everyone, ask the skill's owner.

Adding one of your folders to a skill you can't change makes the copy for you in one step — see [When the skill isn't yours to change](folders.md#when-the-skill-isnt-yours-to-change).

![Skill Studio editing Demo Researcher (Fork), your own copy, with Cancel and Save.](img/skills/04-your-copy.png "Your copy, ready to edit.")

## Create a new skill

1. Click **Create a new skill** (the plus button at the top right).
2. Give it a **Name (lowercase-kebab id)**, for example `meeting-notes`, a **Display name** people will see, and a one-line **Description**.
3. Fill in its instructions, model and tools (below), then click **Save**.

The **Authoring copilot** on the right can draft a skill for you: describe what you want and click **Apply** on its proposal. Nothing is saved until you click **Save**, and **Cancel** leaves without saving.

![Skill Studio creating a new skill: Name meeting-notes, Display name Meeting Notes, a description and a Goal on the left, and the Authoring copilot on the right.](img/skills/05-new-skill.png "A new skill in Skill Studio.")

### Write the instructions

The **Instructions** are what the skill is told before every chat. Every part is optional and they're combined for you:

- **Goal**: the skill's job, in a sentence.
- **Guidelines & behaviour**: tone, approach, what to prioritise.
- **Constraints**: what it must never do.
- **Output format**: how answers should be laid out.
- **Additional instructions**: anything else.

![The Instructions box with Goal, Guidelines & behaviour, Constraints and Output format filled in for a meeting-notes skill.](img/skills/06-instructions.png "The instructions, in parts.")

### Choose the model and tools

- **Language model**: pick a tier — best, pro or lite. Faster, cheaper models suit simple skills; the most capable ones suit hard reasoning.
- **Category** decides which group the skill appears under in the switcher.
- **Tools** are what the skill can do beyond chatting: search the web, read a web page, read your documents, create a Word document, run code, search knowledge bases. Tick only what it needs.

![The Language model picker, the Category picker and the Tools checklist with Search, Documents and Analysis tools.](img/skills/07-model-and-tools.png "Model, category and tools.")

### Let it search knowledge bases

1. In **Tools**, tick **Search knowledge bases**.
2. A **Knowledge bases this skill searches** box appears. Tick the shared knowledge bases it should search.
3. Tick **Each user's own searchable folders** to let everyone who chats with the skill search the folders *they* made searchable — never anyone else's.

The box checks straight away that everyone who can use the skill may search what you picked. To make one of your folders searchable first, see [Make a folder searchable](folders.md#make-a-folder-searchable).

![The Knowledge bases this skill searches box with Each user's own searchable folders and Team handbook ticked, and a check that everyone who can use this skill may search these.](img/skills/08-studio-knowledge-bases.png "Knowledge bases for the skill.")

## Share your skill

New skills are private to you. To let others use one, change **Who can use this skill** in Skill Studio and click **Save**: you can share it with a group, everyone at your email domain, or named people. The options, and what each one means, are in [Sharing and access](sharing-and-access.md#choose-who-can-use-your-skill).

A skill can only search knowledge bases that reach at least the same people. If one doesn't, saving tells you which.
