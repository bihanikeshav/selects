import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { createPortal } from "react-dom";

import "./SelectMenu.css";

export interface SelectMenuOption {
  value: string;
  label: string;
}

interface Props {
  value: string;
  options: readonly SelectMenuOption[];
  onChange: (value: string) => void;
  ariaLabel: string;
  className?: string;
  disabled?: boolean;
}

interface MenuPosition {
  top: number;
  left: number;
  width: number;
}

const VIEWPORT_GUTTER = 8;
const MENU_GAP = 4;

export default function SelectMenu({
  value,
  options,
  onChange,
  ariaLabel,
  className = "",
  disabled = false,
}: Props) {
  const id = useId().replace(/:/g, "");
  const menuId = `select-menu-${id}`;
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [position, setPosition] = useState<MenuPosition | null>(null);

  const selectedIndex = Math.max(
    0,
    options.findIndex((option) => option.value === value),
  );
  const selectedOption = options[selectedIndex] ?? { value, label: value };

  const positionMenu = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;

    const triggerRect = trigger.getBoundingClientRect();
    const menuRect = menuRef.current?.getBoundingClientRect();
    const menuHeight = menuRect?.height ?? Math.min(320, options.length * 40 + 8);
    const width = Math.min(
      Math.max(triggerRect.width, menuRect?.width ?? 156),
      Math.max(120, window.innerWidth - VIEWPORT_GUTTER * 2),
    );
    const left = Math.min(
      Math.max(VIEWPORT_GUTTER, triggerRect.left),
      Math.max(VIEWPORT_GUTTER, window.innerWidth - width - VIEWPORT_GUTTER),
    );
    const canFlip = triggerRect.top - MENU_GAP - menuHeight >= VIEWPORT_GUTTER;
    const fitsBelow = triggerRect.bottom + MENU_GAP + menuHeight <= window.innerHeight - VIEWPORT_GUTTER;
    const top = canFlip && !fitsBelow
      ? triggerRect.top - menuHeight - MENU_GAP
      : Math.min(
          window.innerHeight - VIEWPORT_GUTTER - menuHeight,
          triggerRect.bottom + MENU_GAP,
        );

    setPosition({ top: Math.max(VIEWPORT_GUTTER, top), left, width });
  }, [options.length]);

  useLayoutEffect(() => {
    if (!open) return;
    positionMenu();
  }, [open, positionMenu]);

  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onViewportChange = () => positionMenu();

    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("resize", onViewportChange);
    window.addEventListener("scroll", onViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("resize", onViewportChange);
      window.removeEventListener("scroll", onViewportChange, true);
    };
  }, [open, positionMenu]);

  function choose(index: number) {
    const option = options[index];
    if (!option) return;
    onChange(option.value);
    setOpen(false);
    triggerRef.current?.focus();
  }

  function onTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (disabled || options.length === 0) return;

    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        setActiveIndex(selectedIndex);
        setOpen(true);
      } else {
        const direction = event.key === "ArrowDown" ? 1 : -1;
        setActiveIndex((current) => (current + direction + options.length) % options.length);
      }
      return;
    }

    if (event.key === "Home" || event.key === "End") {
      if (!open) return;
      event.preventDefault();
      setActiveIndex(event.key === "Home" ? 0 : options.length - 1);
      return;
    }

    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) choose(activeIndex);
      else {
        setActiveIndex(selectedIndex);
        setOpen(true);
      }
      return;
    }

    if (event.key === "Escape") {
      if (!open) return;
      event.preventDefault();
      setOpen(false);
    }
  }

  const menu = open && typeof document !== "undefined"
    ? createPortal(
        <div
          ref={menuRef}
          id={menuId}
          className="select-menu-popover"
          role="listbox"
          aria-label={ariaLabel}
          style={{
            top: position?.top ?? 0,
            left: position?.left ?? 0,
            minWidth: position?.width ?? undefined,
            visibility: position ? "visible" : "hidden",
          }}
        >
          {options.map((option, index) => (
            <button
              key={option.value}
              type="button"
              role="option"
              aria-selected={option.value === value}
              className={`select-menu-option${index === activeIndex ? " is-active" : ""}${option.value === value ? " is-selected" : ""}`}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => choose(index)}
            >
              <span>{option.label}</span>
              {option.value === value && <span className="select-menu-check" aria-hidden="true">✓</span>}
            </button>
          ))}
        </div>,
        document.body,
      )
    : null;

  return (
    <span className={`select-menu ${className}`.trim()}>
      <button
        ref={triggerRef}
        type="button"
        className="select-menu-trigger"
        role="combobox"
        aria-label={ariaLabel}
        aria-controls={menuId}
        aria-expanded={open}
        aria-haspopup="listbox"
        disabled={disabled}
        onClick={() => {
          setActiveIndex(selectedIndex);
          setOpen((current) => !current);
        }}
        onKeyDown={onTriggerKeyDown}
      >
        <span className="select-menu-value">{selectedOption.label}</span>
        <span className={`select-menu-chevron${open ? " is-open" : ""}`} aria-hidden="true" />
      </button>
      {menu}
    </span>
  );
}
