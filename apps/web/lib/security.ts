const SAFE_KNOWLEDGE_SOURCE_PREFIX = "/v1/knowledge/chunks/";
const SAFE_API_ORIGIN = "https://customer-care-api.invalid";
const MAX_SOURCE_LOCATOR_LENGTH = 2_048;

/**
 * Source locators are server-generated convenience links, never capabilities.
 * Treat API responses as untrusted anyway so a compromised or malformed model
 * response cannot make the browser request an arbitrary URL.
 */
export function isSafeKnowledgeSourceLocator(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > MAX_SOURCE_LOCATOR_LENGTH ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) {
    return false;
  }

  let locator: URL;
  try {
    locator = new URL(value, SAFE_API_ORIGIN);
  } catch {
    return false;
  }

  const chunkId = locator.pathname.slice(SAFE_KNOWLEDGE_SOURCE_PREFIX.length);
  const isRelative = locator.origin === SAFE_API_ORIGIN;
  const hasExpectedPath = locator.pathname.startsWith(
    SAFE_KNOWLEDGE_SOURCE_PREFIX,
  );
  const hasSingleChunkId =
    chunkId.length > 0 && !chunkId.includes("/") && !/%2f/i.test(chunkId);
  const hasScope = ["product", "region", "language"].every(
    (name) => Boolean(locator.searchParams.get(name)),
  );
  const isUnmodifiedRelativePath =
    value === `${locator.pathname}${locator.search}` && !locator.hash;

  return (
    isRelative &&
    hasExpectedPath &&
    hasSingleChunkId &&
    hasScope &&
    isUnmodifiedRelativePath
  );
}
