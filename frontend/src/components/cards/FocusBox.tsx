import { rowKeys } from "../../lib/stageFocus";

type Props = {
  row: unknown;
  focused: (row: unknown) => boolean;
  onToggle: (row: unknown) => void;
  disabled?: boolean;
};

export default function FocusBox({ row, focused, onToggle, disabled }: Props) {
  const keys = rowKeys(row);
  if (!keys.length) return null;
  const on = focused(row);
  return (
    <input
      type="checkbox"
      checked={on}
      disabled={disabled}
      aria-label={`Focus ${keys[0]}`}
      onChange={() => onToggle(row)}
    />
  );
}
