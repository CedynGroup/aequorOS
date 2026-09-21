"use client";

import type { Editor } from "@tiptap/react";
import {
  Bold,
  Italic,
  List,
  ListOrdered,
  Quote,
  Sigma,
  Table2,
  Underline,
} from "lucide-react";

type ToolbarProps = {
  editor: Editor;
  disabled?: boolean;
  onInsertBlock?: () => void;
  onInsertFact?: () => void;
};

function Button({
  active,
  disabled,
  label,
  onClick,
  children,
}: {
  active?: boolean;
  disabled?: boolean;
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={active ?? false}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex h-8 min-w-8 items-center justify-center gap-1 rounded px-2 text-caption font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        active
          ? "bg-action-light text-action"
          : "text-slate hover:bg-surface hover:text-navy"
      }`}
    >
      {children}
    </button>
  );
}

export default function Toolbar({
  editor,
  disabled = false,
  onInsertBlock,
  onInsertFact,
}: ToolbarProps) {
  const chain = () => editor.chain().focus();
  return (
    <div className="flex flex-wrap items-center gap-0.5 border-b border-border-light px-2 py-1.5">
      <Button
        label="Bold"
        disabled={disabled}
        active={editor.isActive("bold")}
        onClick={() => chain().toggleBold().run()}
      >
        <Bold size={14} aria-hidden />
      </Button>
      <Button
        label="Italic"
        disabled={disabled}
        active={editor.isActive("italic")}
        onClick={() => chain().toggleItalic().run()}
      >
        <Italic size={14} aria-hidden />
      </Button>
      <Button
        label="Underline"
        disabled={disabled}
        active={editor.isActive("underline")}
        onClick={() => chain().toggleUnderline().run()}
      >
        <Underline size={14} aria-hidden />
      </Button>
      <span className="mx-1 h-5 w-px bg-border-light" aria-hidden />
      {([2, 3, 4] as const).map((level) => (
        <Button
          key={level}
          label={`Heading level ${level}`}
          disabled={disabled}
          active={editor.isActive("heading", { level })}
          onClick={() => chain().toggleHeading({ level }).run()}
        >
          H{level}
        </Button>
      ))}
      <span className="mx-1 h-5 w-px bg-border-light" aria-hidden />
      <Button
        label="Bulleted list"
        disabled={disabled}
        active={editor.isActive("bulletList")}
        onClick={() => chain().toggleBulletList().run()}
      >
        <List size={14} aria-hidden />
      </Button>
      <Button
        label="Numbered list"
        disabled={disabled}
        active={editor.isActive("orderedList")}
        onClick={() => chain().toggleOrderedList().run()}
      >
        <ListOrdered size={14} aria-hidden />
      </Button>
      <Button
        label="Quotation"
        disabled={disabled}
        active={editor.isActive("blockquote")}
        onClick={() => chain().toggleBlockquote().run()}
      >
        <Quote size={14} aria-hidden />
      </Button>
      {(onInsertBlock || onInsertFact) && (
        <>
          <span className="mx-1 h-5 w-px bg-border-light" aria-hidden />
          {onInsertBlock && (
            <Button
              label="Insert a set of figures"
              disabled={disabled}
              onClick={onInsertBlock}
            >
              <Table2 size={14} aria-hidden />
              <span className="hidden sm:inline">Figures</span>
            </Button>
          )}
          {onInsertFact && (
            <Button
              label="Cite a figure in this sentence"
              disabled={disabled}
              onClick={onInsertFact}
            >
              <Sigma size={14} aria-hidden />
              <span className="hidden sm:inline">Cite</span>
            </Button>
          )}
        </>
      )}
    </div>
  );
}
