// Shared-build constants without zod, so the Worker can use them without loading the write contracts (./index).
/** Shared PC builds: only the chosen product per slot (by slug) is stored; prices are always read live. */
export const BUILD_SLOTS = ['cpu', 'motherboard', 'ram', 'gpu', 'storage', 'psu', 'case', 'cooler'] as const;
export const SHARED_BUILD_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
