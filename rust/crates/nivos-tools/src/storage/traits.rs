//! MemoryBackend trait for all storage backends.

use nivos_core::{NivosError, RetrievalResult};
use serde_json::Value;

pub trait MemoryBackend: Send + Sync {
    fn backend_id(&self) -> &str;
    fn store(
        &self,
        content: &str,
        source: &str,
        metadata: Option<&Value>,
    ) -> Result<String, NivosError>;
    fn retrieve(
        &self,
        query: &str,
        top_k: usize,
    ) -> Result<Vec<RetrievalResult>, NivosError>;
    fn delete(&self, doc_id: &str) -> Result<bool, NivosError>;
    fn clear(&self) -> Result<(), NivosError>;
    fn count(&self) -> Result<usize, NivosError>;
}
