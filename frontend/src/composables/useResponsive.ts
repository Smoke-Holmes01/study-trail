import { ref, onMounted, onBeforeUnmount } from "vue";
export function useResponsive() {
  const compact = ref(false),
    phone = ref(false),
    touch = ref(false),
    keyboard = ref(false);
  const height = ref("100dvh");
  function update() {
    compact.value = innerWidth < 1024;
    phone.value = innerWidth < 768 || (innerWidth < 1024 && innerHeight <= 500);
    touch.value = matchMedia("(pointer: coarse)").matches;
    const viewport = window.visualViewport;
    keyboard.value =
      !!viewport &&
      viewport.scale === 1 &&
      innerHeight - viewport.height > 150 &&
      !!document.activeElement?.matches("input, textarea, [contenteditable]");
    height.value =
      viewport && viewport.scale === 1 ? `${viewport.height}px` : "100dvh";
  }
  onMounted(() => {
    update();
    window.addEventListener("resize", update);
    window.visualViewport?.addEventListener("resize", update);
    window.visualViewport?.addEventListener("scroll", update);
    document.addEventListener("focusin", update);
    document.addEventListener("focusout", update);
  });
  onBeforeUnmount(() => {
    window.removeEventListener("resize", update);
    window.visualViewport?.removeEventListener("resize", update);
    window.visualViewport?.removeEventListener("scroll", update);
    document.removeEventListener("focusin", update);
    document.removeEventListener("focusout", update);
  });
  return { compact, phone, touch, keyboard, height };
}
