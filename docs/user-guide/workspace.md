---
title: The workbench
summary: See the sources behind an answer, read documents beside the chat, find every result from a conversation, and watch what the assistant is doing.
audience: user
order: 50
surfaces:
  - frontend/src/components/chat/Workbench.tsx
  - frontend/src/components/chat/WorkbenchHome.tsx
  - frontend/src/components/chat/ActivityPanel.tsx
  - frontend/src/components/workspace/SourcesArtefactTab.tsx
  - frontend/src/components/document/
reviewed: 2026-10-16
---

The **Workbench** is the panel that opens beside the chat as soon as there's something to show: the sources an answer used, a document you're reading, or a result the assistant produced. Its tabs sit along the top: **Workspace**, one tab per result (such as **Sources**), **Document** and **Activity**.

![The chat on the left with a question about rooftop solar and the answer. On the right the Workbench is open on its Sources tab, listing two web pages and one document.](img/workspace/01-answer-with-sources.png "An answer, with its sources in the Workbench.")

## See where an answer came from

When the assistant searches the web or your documents, the pages and files it used appear in a **Sources** tab. The Workbench switches to it on its own, so you can check the answer straight away.

1. Look at the numbered list in **Sources**. Each row is one source the answer drew on.
2. Click a web page to open it in a new browser tab.
3. Click a document marked **Open in Document tab** to read it beside the chat.

Sources only lists what the search returned. If a statement in the answer isn't backed by anything here, treat it with care and ask the assistant where it came from.

![The Sources tab: 3 sources — solar-guide.example.org and energy-facts.example.com with an external-link icon, and Phoenix project brief.docx with Open in Document tab.](img/workspace/05-sources-tab.png "Web pages open in your browser; documents open in the Document tab.")

## Read a document beside the chat

The **Document** tab shows one document next to the conversation, so you can read it while you ask about it.

1. Open a document from **Sources**, or click a document in the sidebar under **My Documents**.
2. Switch between **Styled**, which looks like the original file, and **Text**, the plain text the assistant reads.
3. Use **Formatting**, **History** and **Download** along the top to restyle it, see earlier versions, or save a copy.
4. **Conversations** at the bottom lists your other chats about this document.

> [!NOTE]
> Opening a document also adds it to your next message. A chip with its name appears above the chat, and the message box says **Will process:** with the file name, so you can ask about it directly.

![The Document tab showing Phoenix project brief.docx in the Text view, with Formatting, History and Download above it and the brief's headings and assumptions below.](img/workspace/04-document-tab.png "A document open beside the chat.")

## Find every result from this chat

**Workspace** is the Workbench's home tab. It lists every result from this conversation under **Results**, newest first, so nothing gets lost when the assistant produces several.

1. Click **Workspace**.
2. Click a result, or its **Open ›**, to bring its tab to the front.
3. If you have a document open, a **Document** row takes you back to it.

To remove a result you no longer need, click **×** on its tab. You're asked to confirm first, because getting it back means running the question or analysis again.

![The Workspace tab with a Results list holding one entry: Sources, Web pages cited in this answer, with Open on the right.](img/workspace/02-workspace-home.png "Every result from the conversation, in one place.")

## Watch what the assistant is doing

The **Activity** tab shows each step the assistant takes, while it works and afterwards: which tools it called, which documents it read, and when it handed a question to another skill.

1. Click **Activity** while a reply is still coming, or afterwards to see how it got there.
2. Each row is one step, newest at the top, with a green dot when it finished.
3. Click a row to see more about that step.

A dot on the **Activity** tab means something new happened while you were looking at another tab.

![The Activity tab listing a Web search step finished just now, under a Session started marker.](img/workspace/03-activity.png "Each step the assistant took, live.")

## Make more room for the chat

- **Resize:** drag the divider between the chat and the Workbench.
- **Hide:** click the arrow at the top right of the Workbench (**Collapse workbench**). A thin strip stays on the right edge; click it (**Expand workbench**) to bring the Workbench back with everything still in it.
- **On a phone:** the chat and the Workbench share the screen. Use the **Chat** and **Workbench** switch at the top to move between them.

![The chat filling the window, with the Workbench collapsed to a thin strip on the right edge.](img/workspace/06-collapsed.png "The Workbench hidden; the strip on the right brings it back.")

## Documents the assistant creates

When you ask the assistant to write something, such as a summary, a letter or a report, it saves a real Office document to the **Created** folder in **My Documents**. From there you can open it in the **Document** tab, download it, move it, or ask for changes. See [Working with folders](folders.md#delete-a-folder) for how the **Created** folder works.
