export function viewportBounds() {
  const v = window.visualViewport;
  return {
    left: v?.offsetLeft ?? 0,
    top: v?.offsetTop ?? 0,
    width: v?.width ?? innerWidth,
    height: v?.height ?? innerHeight,
  };
}
