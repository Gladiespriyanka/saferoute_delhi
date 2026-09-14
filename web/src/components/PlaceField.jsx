import { useEffect, useId, useRef, useState } from "react";

import { searchPlaces } from "../lib/places.js";

/**
 * An address field with typeahead, saved places and recent destinations.
 *
 * The list is a real listbox: arrow keys move through it, Enter picks,
 * Escape closes, and the active option is announced. Suggestions are
 * debounced and every in-flight request is aborted when a newer keystroke
 * arrives, so a slow response can't overwrite a fresher one.
 */
export default function PlaceField({
  id,
  label,
  placeholder,
  value,
  onChange,
  shortcuts = [],
  icon,
  autoFocus,
}) {
  const listId = useId();
  const [query, setQuery] = useState(value?.label || "");
  const [options, setOptions] = useState([]);
  const [status, setStatus] = useState(null);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const requestRef = useRef(0);
  const abortRef = useRef(null);
  const blurTimer = useRef(null);

  // Keep the visible text in step when the parent sets a place for us
  // (a saved shortcut, or "use my location").
  useEffect(() => {
    setQuery(value?.label || "");
  }, [value]);

  useEffect(() => () => abortRef.current?.abort(), []);

  function close() {
    setOpen(false);
    setOptions([]);
    setStatus(null);
    setActiveIndex(-1);
  }

  function choose(place) {
    setQuery(place.label);
    onChange(place);
    close();
  }

  function handleInput(event) {
    const next = event.target.value;
    setQuery(next);
    onChange(null); // the typed text no longer matches a resolved place
    clearTimeout(blurTimer.current);

    abortRef.current?.abort();
    if (next.trim().length < 2) {
      close();
      return;
    }

    const id = ++requestRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    setOpen(true);
    setStatus("Searching…");

    blurTimer.current = setTimeout(async () => {
      try {
        const found = await searchPlaces(next, { signal: controller.signal });
        if (id !== requestRef.current) return;
        setOptions(found);
        setStatus(found.length ? null : "No matching places");
        setActiveIndex(-1);
      } catch (error) {
        if (error.name === "AbortError" || id !== requestRef.current) return;
        setOptions([]);
        setStatus("Couldn't load suggestions");
      }
    }, 280);
  }

  function handleKeyDown(event) {
    if (!open || !options.length) {
      if (event.key === "Escape") close();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => (index + 1) % options.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => (index <= 0 ? options.length - 1 : index - 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(options[activeIndex >= 0 ? activeIndex : 0]);
    } else if (event.key === "Escape") {
      close();
    }
  }

  return (
    <div className="placeField">
      <label className="eyebrow placeField__label" htmlFor={id}>
        {label}
      </label>

      <div className="placeField__box">
        {icon && <span className="placeField__icon" aria-hidden="true">{icon}</span>}
        <input
          id={id}
          type="text"
          className="placeField__input"
          placeholder={placeholder}
          value={query}
          autoFocus={autoFocus}
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck="false"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={
            activeIndex >= 0 ? `${listId}-option-${activeIndex}` : undefined
          }
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          onBlur={() => setTimeout(close, 140)}
        />
        {query && (
          <button
            type="button"
            className="placeField__clear"
            aria-label={`Clear ${label.toLowerCase()}`}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => {
              setQuery("");
              onChange(null);
              close();
              document.getElementById(id)?.focus();
            }}
          >
            ×
          </button>
        )}

        {open && (
          <ul className="placeField__list" id={listId} role="listbox" aria-label={label}>
            {status && <li className="placeField__status">{status}</li>}
            {options.map((place, index) => (
              <li
                key={`${place.label}-${index}`}
                id={`${listId}-option-${index}`}
                role="option"
                aria-selected={index === activeIndex}
                className={`placeField__option${index === activeIndex ? " is-active" : ""}`}
                onMouseDown={(event) => {
                  event.preventDefault();
                  choose(place);
                }}
                onMouseEnter={() => setActiveIndex(index)}
              >
                <span className="placeField__optionName">{place.name}</span>
                {place.context && (
                  <span className="placeField__optionContext">{place.context}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {shortcuts.length > 0 && (
        <div className="placeField__shortcuts">
          {shortcuts.map((shortcut) => (
            <button
              key={shortcut.key}
              type="button"
              className="placeField__shortcut"
              onClick={() => shortcut.onSelect(choose)}
            >
              {shortcut.icon && <span aria-hidden="true">{shortcut.icon}</span>}
              {shortcut.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
