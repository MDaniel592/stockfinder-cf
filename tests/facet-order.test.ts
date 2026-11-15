import { describe, expect, it } from 'vitest';
import { compareFacetValues } from '../packages/api-contracts/catalog-filters';

const sorted = (values: string[]) => [...values].sort(compareFacetValues);

describe('facet value ordering', () => {
  it('orders numeric-like values by magnitude, not as strings', () => {
    expect(sorted(['10', '12', '4', '6', '8', '24'])).toEqual(['4', '6', '8', '10', '12', '24']);
    expect(sorted(['1000 W', '650 W', '85 W', '1200 W'])).toEqual(['85 W', '650 W', '1000 W', '1200 W']);
    expect(sorted(['1 TB', '500 GB', '512 GB', '2 TB', '240 GB'])).toEqual(['240 GB', '500 GB', '512 GB', '1 TB', '2 TB']);
    expect(sorted(['6000 MT/s', '3200 MT/s', '800 MT/s'])).toEqual(['800 MT/s', '3200 MT/s', '6000 MT/s']);
    expect(sorted(['CL36', 'CL16', 'CL9', 'CL100'])).toEqual(['CL9', 'CL16', 'CL36', 'CL100']);
    expect(sorted(['16 GB', '8 GB', '128 GB', '4 GB'])).toEqual(['4 GB', '8 GB', '16 GB', '128 GB']);
  });
  it('falls back to alphabetical order for text', () => {
    expect(sorted(['Sí', 'No'])).toEqual(['No', 'Sí']);
    expect(sorted(['PCIe 5.0', 'PCIe 4.0'])).toEqual(['PCIe 4.0', 'PCIe 5.0']);
  });
});
