import type { Root, RootContent } from "hast";
import type { VFile } from "vfile";

/** Keep each explicit Markdown list number; CommonMark otherwise keeps only the first. */
export function preserveOrderedListValues() {
  return (tree: Root, file: VFile) => {
    const source = String(file.value);
    function visit(node: Root | RootContent, ordered = false) {
      if (node.type === "element" && node.tagName === "li" && ordered) {
        const offset = node.position?.start.offset;
        const marker = offset == null ? null : source.slice(offset).match(/^(\d{1,9})[.)](?=\s|$)/);
        if (marker) node.properties.value = Number(marker[1]);
      }
      if ("children" in node) {
        for (const child of node.children) {
          visit(child, node.type === "element" && node.tagName === "ol");
        }
      }
    }
    visit(tree);
  };
}
