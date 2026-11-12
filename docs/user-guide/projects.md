---
title: Working with projects
summary: Group the documents and chats for one piece of work, give it its own instructions, and chat with only its documents.
audience: user
order: 35
surfaces:
  - frontend/src/components/projects/
  - frontend/src/app/projects/
  - frontend/src/components/chat/ContextBanner.tsx
  - frontend/src/components/chat/ChatShell.tsx
  - frontend/src/components/doc-browser/DocListItem.tsx
  - frontend/src/components/doc-browser/DocListFolder.tsx
  - backend/adk/project_tools.py
  - backend/adk/project_floor.py
reviewed: 2026-10-27
---

A project gathers the documents for one piece of work, its own instructions, and the chats about them. A document can be in several projects and stays in its folder: a project only points at it. Projects are private to you.

Your projects are in the sidebar under **Projects**, above **Chats**. Open the sidebar with the button at the top left (**Show conversations and files**).

## Make a project

1. In the sidebar, under **Projects**, click **New project**.
2. Type a name and press `Enter`, or press `Esc` to cancel. Two of your projects can't share a name.
3. The project opens on **Set up your project**, with a **Getting started** list: add documents, optionally add instructions, then start chatting.

You can also start a project from something you already have:

- **From a chat:** hover over the chat under **Chats**, click its **⋯** (**More actions for …**), then **Make this a project**. The project is named after the chat, the chat moves into it, and so do the documents the chat used.
- **From a folder:** click **⋯** on the folder, then **New project from folder**. The folder's documents join the project *as they are today*: files you upload to the folder later aren't added.
- **From a document:** hover over the document, click **⋯**, then **Add to project…** and **+ New project…**. The name is filled in from the document; press `Enter` to create the project with the document in it.
- **By asking:** in any chat, ask the assistant, for example "Make a project called Supplier review with the shortlist and the budget". It shows a card with what it created.

> [!TIP]
> With more than eight projects, the sidebar shows **See all** with the number. It opens the page listing every project, most recently opened first.

## Add documents to a project

On the project's settings page:

1. Click **Add documents**.
2. On **Your documents**, tick the documents you want. Narrow the list with **Search by name**, or pick a folder instead of **Recent**. Documents already in the project are marked **In this project**.
3. Click **Add** (it shows how many you've ticked).

The **Shared library** tab lists the shared documents your workspace offers to the project's assistant, if any. Picking one copies it into your own library first, then adds it.

To add a file that isn't uploaded yet, drop it onto the upload area under the project's documents: it's saved to your library and added to the project.

Other ways to add a document:

- **By dragging:** drag the document from **Files** onto the project in the sidebar. The project is highlighted while you're over it, and a line underneath says it was added. Dragging onto a project *adds* the document; dragging onto a folder *moves* it.
- **From the document's menu:** click **⋯**, then **Add to project…**, and tick the project. Untick it to take the document out again.
- **By asking:** "Add the kick-off notes to the Phoenix project". If a name matches more than one of your documents, the assistant asks which one you mean instead of guessing.

A document in a project shows a small project label beside its name in the sidebar.

## Chat in a project

Click a project's name in the sidebar. The chat screen opens in that project:

- The line above the conversation reads **In project** with the project's name, how many documents it has, **instructions on** if instructions are set, and a **Project settings** link.
- The message box says *Ask about* followed by the project's name.
- **Chats** lists only this project's chats. A chat that used one of the project's documents is listed too, with a note saying which document.
- On a new chat, the Workbench shows the project's documents. Click one to open it, or **Continue** to go back to the conversation you last had about it.
- **New chat** starts another chat in the same project, with the assistant set under [New chats open on](#give-a-project-instructions).

Chats in a project remember what you tell them there, separately from your other chats. See [What the assistant remembers](memory.md#what-the-assistant-remembers).

### What a project chat can read

In a project chat the assistant reads *only the project's documents*, plus any document you open in that chat yourself. It doesn't search your other documents, your searchable folders or shared libraries. Web search and the skill's other tools still work.

- If the answer isn't in the project's documents, the assistant says so.
- If it needs a document that isn't in the project, it asks you first, and only adds it if you say yes.

Where search is switched on, the project's documents are prepared for search automatically. The settings page shows **Preparing 3 of 5 for search…** while that happens and **Ready for search** when it's done. You don't need to do anything, and chats can already open the documents by name in the meantime. A document with no readable text can't be searched. Scans count as readable: their text is read from the page pictures, so they are searched like any other document.

## Move a chat into or out of a project

1. Hover over the chat under **Chats** and click its **⋯** (**More actions for …**).
2. Under **Move to project**, click the project. The current project is ticked.
3. To take a chat out of its project, choose **No project**.

A line under the list says what happened, with **Open** to go to the project.

## Leave a project

To go back to ordinary chats, click **✕** at the end of the **In project** line above the conversation. The next chat isn't in any project. In the sidebar, **Show all chats** above the project's chats does the same.

## Give a project instructions

1. Open **Project settings**: the link in the **In project** line, or the gear that appears when you hover over the project in the sidebar.
2. Under **Project instructions**, say what the project is about and how you want answers, for example which clauses matter or what language to answer in.
3. Click outside the box. It saves on its own and says **Saved**.

Every chat in the project uses them, on top of [your own instructions](memory.md#tell-the-assistant-about-yourself).

**New chats open on** sets which assistant a chat in this project starts with. Leave it on **Your default assistant** to use the usual one.

## Rename or delete a project

- **Rename:** on the settings page, click the project's name, type the new one, and press `Enter` (or `Esc` to cancel).
- **Delete:** at the bottom of the settings page, click **Delete project…**, then **Delete project** to confirm, or **Cancel**.

Deleting a project keeps its documents and its chats: they're still in your library and your history. What the assistant remembered from the project's chats *is* deleted.

To take a single document out of a project without deleting anything, untick the project under the document's **Add to project…**, or remove it from the project's document list on the settings page.
