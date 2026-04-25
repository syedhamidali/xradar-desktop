import { tableFromIPC, type Table } from 'apache-arrow';

/**
 * Decode an Apache Arrow IPC buffer into an Arrow Table.
 *
 * @param buffer - The raw Arrow IPC bytes (ArrayBuffer or Uint8Array).
 * @returns The deserialized Arrow Table.
 */
export function decodeArrowIPC(buffer: ArrayBuffer | Uint8Array): Table {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  return tableFromIPC(bytes);
}

/**
 * Convert an Arrow Table to an array of plain JS objects.
 *
 * Each row becomes an object whose keys are the column names
 * and whose values are the column values for that row.
 *
 * @param table - An Apache Arrow Table.
 * @returns An array of row objects.
 */
export function arrowToArray(table: Table): Record<string, any>[] {
  const result: Record<string, any>[] = [];
  const fields = table.schema.fields;

  for (let rowIdx = 0; rowIdx < table.numRows; rowIdx++) {
    const row: Record<string, any> = {};
    for (const field of fields) {
      const column = table.getChild(field.name);
      if (column) {
        row[field.name] = column.get(rowIdx);
      }
    }
    result.push(row);
  }

  return result;
}

/**
 * Extract a single column from an Arrow Table as a typed array.
 *
 * @param table - An Apache Arrow Table.
 * @param columnName - The name of the column to extract.
 * @returns A flat array of the column values, or null if the column does not exist.
 */
export function arrowColumnToArray(table: Table, columnName: string): any[] | null {
  const column = table.getChild(columnName);
  if (!column) return null;

  const values: any[] = [];
  for (let i = 0; i < column.length; i++) {
    values.push(column.get(i));
  }
  return values;
}
