"use client";

import { useEffect, useState, type ReactNode } from "react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import * as Dialog from "@radix-ui/react-dialog";
import { Check, ChevronRight, FileSearch, FileText, FolderKanban, Gauge, Lock, Paperclip, Plus, Volume2 } from "lucide-react";
import Link from "next/link";
import type { RecentDocument } from "@/hooks/useRecentDocuments";
import { useProjects } from "@/hooks/useProjects";
import { useAutoReadAloud } from "@/hooks/useAutoReadAloud";
import { Switch } from "@/components/settings/Switch";
import { cn } from "@/lib/utils";
import { FLOATING_SURFACE } from "@/lib/surfaces";
import type { MenuOption, ModelOptions } from "@/lib/modelChoice";

interface ComposerAddMenuProps {
  /** Open the file picker (the composer's upload). */
  onUpload: () => void;
  /** The user's recent documents: "Add from your files". */
  recentDocuments: RecentDocument[];
  onOpenDocument: (doc: RecentDocument) => void;
  /** Show the whole library (opens the sidebar on the files). */
  onBrowseFiles: () => void;
  /** Scope a new chat to a project. Omitted inside a project already. */
  onUseProject?: (projectId: string) => void;
  /** Offer "Read replies aloud" (the deployment and the skill both have a voice). */
  readAloud: boolean;
  /** The Model row (lib/modelChoice). Omitted or `show: false`: no row. */
  model?: ModelPicker;
}

export interface ModelPicker {
  options: ModelOptions;
  /** The current pick: "auto", "fast", "thorough" or "model:<id>". */
  value: string;
  /** Short name of the current pick for the row ("Auto", "Thorough", …). */
  valueLabel: string;
  onChange: (value: string) => void;
}

const ITEM =
  "flex min-h-[40px] w-full cursor-pointer select-none items-center gap-3 rounded-[var(--radius)] px-3 text-left text-sm text-foreground outline-none data-[highlighted]:bg-muted data-[state=open]:bg-muted";
const CONTENT_EXTRA = "min-w-[16rem] max-w-[20rem] p-1 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:zoom-in-95";

/**
 * The composer's + : everything optional about a message in one place —
 * upload, one of your files, a project, read aloud. A dropdown on desktop; a
 * bottom sheet with large rows on a phone.
 */
export function ComposerAddMenu(props: ComposerAddMenuProps) {
  const phone = useIsPhone();
  return phone ? <AddSheet {...props} /> : <AddDropdown {...props} />;
}

function Trigger(rest: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      aria-label="Add files and more"
      title="Add files and more"
      className="flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring data-[state=open]:bg-muted data-[state=open]:text-foreground"
      {...rest}
    >
      <Plus className="h-5 w-5" aria-hidden />
    </button>
  );
}

function AddDropdown({ onUpload, recentDocuments, onOpenDocument, onBrowseFiles, onUseProject, readAloud, model }: ComposerAddMenuProps) {
  const { projects } = useProjects();
  const autoRead = useAutoReadAloud();
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <Trigger />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content side="top" align="start" sideOffset={8} className={`${FLOATING_SURFACE} ${CONTENT_EXTRA}`}>
          <DropdownMenu.Item className={ITEM} onSelect={onUpload}>
            <Paperclip className="h-4 w-4 text-muted-foreground" aria-hidden />
            Upload a file
          </DropdownMenu.Item>
          <DropdownMenu.Sub>
            <DropdownMenu.SubTrigger className={ITEM}>
              <FileSearch className="h-4 w-4 text-muted-foreground" aria-hidden />
              Add from your files
              <ChevronRight className="ml-auto h-4 w-4 text-muted-foreground" aria-hidden />
            </DropdownMenu.SubTrigger>
            <DropdownMenu.Portal>
              <DropdownMenu.SubContent sideOffset={6} className={`${FLOATING_SURFACE} ${CONTENT_EXTRA}`}>
                {recentDocuments.length > 0 ? (
                  <DropdownMenu.Label className="px-3 pb-1 pt-1.5 text-xs text-muted-foreground">Recent</DropdownMenu.Label>
                ) : null}
                {recentDocuments.slice(0, 5).map((d) => (
                  <DropdownMenu.Item key={d.id} className={ITEM} onSelect={() => onOpenDocument(d)}>
                    <FileText className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                    <span className="min-w-0 truncate">{d.originalFilename}</span>
                  </DropdownMenu.Item>
                ))}
                <DropdownMenu.Item className={cn(ITEM, "text-muted-foreground")} onSelect={onBrowseFiles}>
                  Browse all files…
                </DropdownMenu.Item>
              </DropdownMenu.SubContent>
            </DropdownMenu.Portal>
          </DropdownMenu.Sub>
          {onUseProject ? (
            <DropdownMenu.Sub>
              <DropdownMenu.SubTrigger className={ITEM}>
                <FolderKanban className="h-4 w-4 text-muted-foreground" aria-hidden />
                Use a project
                <ChevronRight className="ml-auto h-4 w-4 text-muted-foreground" aria-hidden />
              </DropdownMenu.SubTrigger>
              <DropdownMenu.Portal>
                <DropdownMenu.SubContent sideOffset={6} className={`${FLOATING_SURFACE} ${CONTENT_EXTRA}`}>
                  {projects.map((p) => (
                    <DropdownMenu.Item key={p.id} className={ITEM} onSelect={() => onUseProject(p.id)}>
                      <span className="min-w-0 truncate">{p.name}</span>
                    </DropdownMenu.Item>
                  ))}
                  <DropdownMenu.Item asChild className={cn(ITEM, "text-muted-foreground")}>
                    <Link href="/projects">{projects.length > 0 ? "All projects…" : "Create a project…"}</Link>
                  </DropdownMenu.Item>
                </DropdownMenu.SubContent>
              </DropdownMenu.Portal>
            </DropdownMenu.Sub>
          ) : null}
          {model?.options.show ? <ModelSubmenu model={model} /> : null}
          {readAloud ? (
            <>
              <DropdownMenu.Separator className="my-1 h-px bg-border" />
              <DropdownMenu.Item
                className={ITEM}
                // A switch row: toggles in place, the menu stays open.
                onSelect={(e) => {
                  e.preventDefault();
                  autoRead.toggle();
                }}
              >
                <Volume2 className="h-4 w-4 text-muted-foreground" aria-hidden />
                Read replies aloud
                <span className="ml-auto" aria-hidden>
                  <SwitchVisual on={autoRead.enabled} />
                </span>
                <span className="sr-only">{autoRead.enabled ? "(on)" : "(off)"}</span>
              </DropdownMenu.Item>
            </>
          ) : null}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

function AddSheet({ onUpload, recentDocuments, onOpenDocument, onBrowseFiles, onUseProject, readAloud, model }: ComposerAddMenuProps) {
  const [open, setOpen] = useState(false);
  const { projects } = useProjects();
  const autoRead = useAutoReadAloud();
  const run = (fn: () => void) => () => {
    setOpen(false);
    fn();
  };
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <Trigger />
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-foreground/30 motion-safe:animate-in motion-safe:fade-in-0" />
        <Dialog.Content
          aria-describedby={undefined}
          // Focus the sheet, not its first row: on a touch screen a ring on
          // "Upload a file" reads as already chosen.
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            (e.currentTarget as HTMLElement).focus();
          }}
          className="fixed inset-x-0 bottom-0 z-50 max-h-[80dvh] overflow-y-auto overscroll-contain rounded-t-2xl bg-background p-2 pb-[max(1rem,env(safe-area-inset-bottom))] shadow-lg motion-safe:animate-in motion-safe:slide-in-from-bottom"
        >
          <Dialog.Title className="sr-only">Add to your message</Dialog.Title>
          <div aria-hidden className="mx-auto mb-2 mt-1 h-1 w-10 rounded-full bg-border" />
          <SheetRow icon={<Paperclip className="h-5 w-5" aria-hidden />} onClick={run(onUpload)}>
            Upload a file
          </SheetRow>
          <SheetGroup label="Add from your files">
            {recentDocuments.slice(0, 4).map((d) => (
              <SheetRow key={d.id} icon={<FileText className="h-5 w-5" aria-hidden />} onClick={run(() => onOpenDocument(d))}>
                <span className="min-w-0 truncate">{d.originalFilename}</span>
              </SheetRow>
            ))}
            <SheetRow icon={<FileSearch className="h-5 w-5" aria-hidden />} onClick={run(onBrowseFiles)}>
              Browse all files
            </SheetRow>
          </SheetGroup>
          {onUseProject && projects.length > 0 ? (
            <SheetGroup label="Use a project">
              {projects.slice(0, 5).map((p) => (
                <SheetRow key={p.id} icon={<FolderKanban className="h-5 w-5" aria-hidden />} onClick={run(() => onUseProject(p.id))}>
                  <span className="min-w-0 truncate">{p.name}</span>
                </SheetRow>
              ))}
            </SheetGroup>
          ) : null}
          {model?.options.show ? (
            <SheetGroup label="Model">
              {model.options.locked ? (
                <p className="flex items-start gap-3 px-3 py-3 text-[15px] text-muted-foreground">
                  <Lock className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
                  {model.options.locked}
                </p>
              ) : (
                <div role="radiogroup" aria-label="Model">
                  {model.options.levels.map((o) => (
                    <SheetChoice key={o.value} option={o} checked={model.value === o.value} onClick={run(() => model.onChange(o.value))} />
                  ))}
                  {model.options.models.length > 0 ? (
                    <p className="px-3 pb-1 pt-2 text-xs text-muted-foreground">Specific model</p>
                  ) : null}
                  {model.options.models.map((o) => (
                    <SheetChoice key={o.value} option={o} checked={model.value === o.value} onClick={run(() => model.onChange(o.value))} />
                  ))}
                </div>
              )}
            </SheetGroup>
          ) : null}
          {readAloud ? (
            <div className="mt-1 flex min-h-[52px] items-center gap-3 border-t border-border px-3 text-[15px]">
              <Volume2 className="h-5 w-5 text-muted-foreground" aria-hidden />
              <span className="flex-1">Read replies aloud</span>
              <Switch label="Read replies aloud" checked={autoRead.enabled} onChange={() => autoRead.toggle()} />
            </div>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

const RADIO =
  "flex w-full cursor-pointer select-none items-start gap-2.5 rounded-[var(--radius)] px-3 py-2 text-left text-sm text-foreground outline-none data-[highlighted]:bg-muted";

/** + → Model ›: Auto / Fast / Thorough, then specific models if opted in. */
function ModelSubmenu({ model }: { model: ModelPicker }) {
  const { options } = model;
  return (
    <DropdownMenu.Sub>
      <DropdownMenu.SubTrigger className={ITEM}>
        <Gauge className="h-4 w-4 text-muted-foreground" aria-hidden />
        Model
        <span className="ml-auto max-w-[8rem] truncate text-xs text-muted-foreground">{model.valueLabel}</span>
        <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden />
      </DropdownMenu.SubTrigger>
      <DropdownMenu.Portal>
        <DropdownMenu.SubContent sideOffset={6} className={`${FLOATING_SURFACE} ${CONTENT_EXTRA} max-h-[min(70vh,32rem)] overflow-y-auto`}>
          {options.locked ? (
            <p className="flex items-start gap-2.5 px-3 py-2 text-sm text-muted-foreground">
              <Lock className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              {options.locked}
            </p>
          ) : (
            <DropdownMenu.RadioGroup value={model.value} onValueChange={model.onChange}>
              {options.levels.map((o) => (
                <ModelRadio key={o.value} option={o} />
              ))}
              {options.models.length > 0 ? (
                <>
                  <DropdownMenu.Separator className="my-1 h-px bg-border" />
                  <DropdownMenu.Label className="px-3 pb-1 pt-1.5 text-xs text-muted-foreground">Specific model</DropdownMenu.Label>
                  {options.models.map((o) => (
                    <ModelRadio key={o.value} option={o} />
                  ))}
                </>
              ) : null}
            </DropdownMenu.RadioGroup>
          )}
        </DropdownMenu.SubContent>
      </DropdownMenu.Portal>
    </DropdownMenu.Sub>
  );
}

function ModelRadio({ option }: { option: MenuOption }) {
  return (
    <DropdownMenu.RadioItem value={option.value} className={RADIO}>
      <span className="mt-0.5 h-4 w-4 shrink-0 text-primary">
        <DropdownMenu.ItemIndicator>
          <Check className="h-4 w-4" aria-hidden />
        </DropdownMenu.ItemIndicator>
      </span>
      <span className="min-w-0">
        <span className="block">{option.title}</span>
        {option.detail ? <span className="block text-xs text-muted-foreground">{option.detail}</span> : null}
      </span>
    </DropdownMenu.RadioItem>
  );
}

function SheetChoice({ option, checked, onClick }: { option: MenuOption; checked: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      onClick={onClick}
      className="flex min-h-[52px] w-full items-center gap-3 rounded-[var(--radius)] px-3 py-1.5 text-left text-[15px] text-foreground hover:bg-muted"
    >
      <span className="h-5 w-5 shrink-0 text-primary">{checked ? <Check className="h-5 w-5" aria-hidden /> : null}</span>
      <span className="min-w-0">
        <span className="block">{option.title}</span>
        {option.detail ? <span className="block text-xs text-muted-foreground">{option.detail}</span> : null}
      </span>
    </button>
  );
}

function SheetGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="mt-1 border-t border-border pt-1">
      <p className="px-3 pb-1 pt-2 text-xs text-muted-foreground">{label}</p>
      {children}
    </div>
  );
}

function SheetRow({ icon, onClick, children }: { icon: ReactNode; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex min-h-[52px] w-full items-center gap-3 rounded-[var(--radius)] px-3 text-left text-[15px] text-foreground hover:bg-muted"
    >
      <span className="text-muted-foreground">{icon}</span>
      {children}
    </button>
  );
}

/** The look of a switch, for a menu row that is itself the control. */
function SwitchVisual({ on }: { on: boolean }) {
  return (
    <span className={cn("relative inline-flex h-5 w-9 items-center rounded-full transition-colors", on ? "bg-primary" : "bg-border")}>
      <span
        className={cn("inline-block h-4 w-4 rounded-full bg-background shadow-sm transition-transform", on ? "translate-x-[18px]" : "translate-x-0.5")}
      />
    </span>
  );
}

function useIsPhone(): boolean {
  const [phone, setPhone] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia?.("(max-width: 639px)");
    if (!mq) return;
    const update = () => setPhone(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  return phone;
}
