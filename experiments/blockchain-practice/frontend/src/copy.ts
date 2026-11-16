/** Display copy only: keep hashes, numbers, URLs and signed payloads untouched. */
export const uiText = (value: string) => value.replace(/([가-힣])\.(?=\s|$)/g, "$1");
