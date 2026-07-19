/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * Copyright 2024 Øivind Loe
 * See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
 * ~
 **/
/**
 * Install the page-local bridge that turns renderer readiness events into state updates.
 * This classic script must load before Alpine initializes the session component.
 */
((browserWindow) => {
  const runtimeWindow = /** @type {any} */ (browserWindow);
  const readinessEvent = 'chief:rich-content-ready';

  /** Report whether the documented rich-content renderer is currently callable. */
  const rendererIsReady = () =>
    typeof runtimeWindow.chiefRichContent?.renderRichContent === 'function';

  /**
   * Observe renderer readiness and return an idempotent disposal callback.
   * Registering before the initial read closes the race with a concurrently completing ESM bundle.
   */
  const watchRichContentReadiness = (onChange) => {
    let watching = true;
    const syncReadiness = () => {
      if (watching) {
        onChange(rendererIsReady());
      }
    };
    browserWindow.addEventListener(readinessEvent, syncReadiness);
    syncReadiness();

    // Stop this component's readiness updates without disturbing other session pages.
    return () => {
      if (!watching) {
        return;
      }
      watching = false;
      browserWindow.removeEventListener(readinessEvent, syncReadiness);
    };
  };

  /**
   * Run one rich-output attempt and let only its unique record handle its own failure.
   * The WeakMap belongs to one Alpine session; deleting an entry cancels ownership.
   *
   * @param {WeakMap<Element, {source: string, result: Promise<boolean>}>} attempts
   * @param {Element} element
   * @param {string} source
   * @param {(element: Element, source: string) => boolean | Promise<boolean>} render
   * @param {(element: Element, source: string) => void} showFallback
   * @returns {Promise<boolean>}
   */
  const renderRichOutputAttempt = (attempts, element, source, render, showFallback) => {
    const currentAttempt = attempts.get(element);
    if (currentAttempt?.source === source) {
      return currentAttempt.result;
    }

    const attempt = {
      source,
      result: Promise.resolve(false),
    };
    attempts.set(element, attempt);

    // Degrade only while this exact attempt still owns the element.
    const handleFailure = () => {
      if (attempts.get(element) === attempt) {
        attempts.delete(element);
        showFallback(element, source);
      }
      return false;
    };

    try {
      attempt.result = Promise.resolve(render(element, source)).catch(handleFailure);
    } catch {
      attempt.result = Promise.resolve(handleFailure());
    }
    return attempt.result;
  };

  runtimeWindow.chiefRichContentLifecycle = {
    readinessEvent,
    renderRichOutputAttempt,
    watchRichContentReadiness,
  };
})(window);
