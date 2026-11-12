---
title: Instructions and memory
summary: Tell the assistant about yourself once, and see how it remembers, recalls and forgets things across chats.
audience: user
order: 37
surfaces:
  - frontend/src/components/projects/YourInstructionsCard.tsx
  - frontend/src/components/memory/MemoryList.tsx
  - frontend/src/app/settings/memory/
  - frontend/src/components/projects/InstructionsEditor.tsx
  - frontend/src/app/settings/instructions/
  - frontend/src/components/chat/ContextBanner.tsx
  - backend/adk/memory_tools.py
  - backend/adk/memory_autosave.py
  - backend/adk/project_context.py
reviewed: 2026-10-27
---

There are two ways the assistant knows things about you between chats: *your instructions*, which you write and which apply to every chat, and *memory*, which it builds up from your conversations.

## Tell the assistant about yourself

**Your instructions** are a short note the assistant reads at the start of every chat, whichever skill you use: who you are, what you work on, how you like answers. For example, "I work in finance. Answer in short bullet points and quote figures with their source."

1. Open the account menu (your initial at the top right) and choose **Settings**. **Instructions** is the first section.
2. Type your instructions.
3. Click outside the box. It saves on its own and says **Saved**. If saving fails, the message says why, with **Retry**.

While they're set, the line above the conversation reads **Your instructions on**, with **Edit** to change them. A change applies to new messages within a minute. To stop using them, clear the box.

Your instructions can steer tone, language and focus, but they can't override a skill's own rules. They can be up to 4,000 characters.

> [!TIP]
> For one piece of work rather than everything, use a project's instructions instead. See [Give a project instructions](projects.md#give-a-project-instructions).

## What the assistant remembers

Where memory is switched on, the assistant keeps notes from your conversations so later chats can build on them. Once a conversation pauses for a few minutes, the key points of what you and the assistant *said* are saved. The contents of documents it read and the results of tools it used are not saved.

At the start of each turn the assistant looks up anything relevant it remembers, and it can search its memory when you refer to something from an earlier chat.

- **Your memory is yours.** Other people's chats don't recall it, and yours don't recall theirs. In a conversation someone else shared with you, memory belongs to the conversation's owner.
- **Projects have their own memory.** In a [project chat](projects.md#chat-in-a-project) the assistant remembers and recalls only that project's notes, and your other chats don't see them. Deleting a project deletes its memory.
- **Some skills don't use memory.** A skill can be set up without it.

> [!NOTE]
> Deleting a conversation doesn't delete what was remembered from it. To remove something, delete it under [Memory in Settings](#see-and-delete-what-it-remembers), or ask the assistant to forget it (below).

## See and delete what it remembers

1. Open the account menu (your initial at the top right), choose **Settings** and go to **Memory**.
2. The page lists what the assistant remembers about you, newest first, with the date each was saved. With a long list, click **Show more**.
3. To remove one, click **Delete** on it, then **Delete** again to confirm. It's gone straight away.
4. To remove everything, click **Forget everything…** at the bottom, type `forget`, and click **Forget everything**. This can't be undone.

A project's memory is listed separately, under **Project memory** on the project's settings page, with the same buttons. Deleting there only affects that project.

New memories appear a few minutes after a conversation pauses, so something you said a moment ago may not be listed yet.

## Ask it to remember something

Say it in the chat, for example "Remember that I prefer answers in Spanish" or "Remember that my team is the Northern region". The assistant saves that one fact and tells you what it saved.

In a project chat, the fact is saved to that project's memory, and the reply says so.

## Ask it to forget something

1. Ask, for example "Forget what you know about the Phoenix codename", or "What do you remember about me?" to see the list.
2. The assistant shows what it found and asks which to forget. Nothing is deleted yet.
3. Tell it which ones. Only those are deleted, and it confirms.

In a project chat, this works on that project's memory only. To forget something from your personal memory, ask in a chat that isn't in a project.
