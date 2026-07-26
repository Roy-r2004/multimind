import { Check, ChevronsUpDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SCRAPING_COUNTRIES } from "@/lib/scraping/countries";
import { cn } from "@/lib/utils";

export function CountrySelector({
  value,
  onValueChange,
  disabled = false,
}: {
  value: string;
  onValueChange: (code: string) => void;
  disabled?: boolean;
}) {
  const selected = SCRAPING_COUNTRIES.find((country) => country.code === value);
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-label="Country"
          aria-expanded={false}
          disabled={disabled}
          className="w-full justify-between font-normal"
        >
          {selected ? `${selected.name} (${selected.code})` : "Select a country"}
          <ChevronsUpDown className="ml-2 size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0" align="start">
        <Command>
          <CommandInput placeholder="Search countries or ISO code..." />
          <CommandList>
            <CommandEmpty>No country found.</CommandEmpty>
            {SCRAPING_COUNTRIES.map((country) => (
              <CommandItem
                key={country.code}
                value={`${country.name} ${country.code}`}
                onSelect={() => onValueChange(country.code)}
              >
                <Check
                  className={cn("size-4", country.code === value ? "opacity-100" : "opacity-0")}
                />
                <span>{country.name}</span>
                <span className="ml-auto text-xs text-muted-foreground">{country.code}</span>
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
