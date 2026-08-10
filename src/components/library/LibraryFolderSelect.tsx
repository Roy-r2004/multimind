import type { ApiLibraryFolder } from "@/lib/api/types";
import { flattenLibraryFolderOptions } from "@/lib/libraryUi";
import { cn } from "@/lib/utils";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/** Sentinel value for Radix Select (empty string is not allowed). */
export const LIBRARY_FOLDER_NONE = "__none__";

type LibraryFolderSelectProps = {
  folders: ApiLibraryFolder[];
  value: string | null;
  onChange: (folderId: string | null) => void;
  disabled?: boolean;
  id?: string;
  className?: string;
  placeholder?: string;
};

export function LibraryFolderSelect({
  folders,
  value,
  onChange,
  disabled,
  id,
  className,
  placeholder = "No folder",
}: LibraryFolderSelectProps) {
  const options = flattenLibraryFolderOptions(folders);

  return (
    <Select
      value={value ?? LIBRARY_FOLDER_NONE}
      onValueChange={(next) => onChange(next === LIBRARY_FOLDER_NONE ? null : next)}
      disabled={disabled}
    >
      <SelectTrigger id={id} className={cn("w-full", className)}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={LIBRARY_FOLDER_NONE}>{placeholder}</SelectItem>
        {options.map((option) => (
          <SelectItem key={option.id} value={option.id}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
