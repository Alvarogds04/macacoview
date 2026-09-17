use serde::Serialize;

/// Maximum number of history samples (5 minutes at 1 Hz).
pub const RING_BUFFER_CAPACITY: usize = 300;

/// A single history sample containing only sparkline-relevant data.
#[derive(Debug, Clone, Serialize)]
pub struct HistorySample {
    pub timestamp: String,
    pub used_gib: f64,
    pub available_gib: f64,
    pub group_rss_gib: Vec<f64>, // ordered: pi, hermes, firefox, system, other
}

/// A fixed-capacity ring buffer that keeps the last N samples.
/// Oldest samples are dropped when the buffer is full.
#[derive(Debug, Clone)]
pub struct RingBuffer<T> {
    samples: Vec<T>,
    capacity: usize,
}

impl<T> RingBuffer<T> {
    /// Create a new ring buffer with the given capacity.
    pub fn new(capacity: usize) -> Self {
        Self {
            samples: Vec::with_capacity(capacity),
            capacity,
        }
    }

    /// Insert a sample, dropping the oldest if at capacity.
    ///
    /// A `Vec` keeps `samples()` a contiguous slice, which keeps the
    /// once-per-second serialization cheap. The `remove(0)` shift is
    /// deliberate: at 300 samples it moves a few kilobytes per second.
    pub fn push(&mut self, sample: T) {
        if self.samples.len() >= self.capacity {
            self.samples.remove(0);
        }
        self.samples.push(sample);
    }

    /// Returns a reference to all samples in insertion order (oldest first).
    pub fn samples(&self) -> &[T] {
        &self.samples
    }
}

impl<T> Default for RingBuffer<T> {
    fn default() -> Self {
        Self::new(RING_BUFFER_CAPACITY)
    }
}

/// Ordered group keys that the sparkline data expects.
pub const GROUP_KEYS: &[&str] = &["pi", "hermes", "firefox", "system", "other"];

/// Build a history sample from stats data, extracting only the sparkline fields.
pub fn sample_from_stats(
    memory_used: f64,
    memory_available: f64,
    groups: &std::collections::HashMap<String, crate::stats::Group>,
) -> HistorySample {
    let group_rss: Vec<f64> = GROUP_KEYS
        .iter()
        .map(|k| groups.get(*k).map(|g| g.rss_gib).unwrap_or(0.0))
        .collect();

    HistorySample {
        timestamp: chrono::Utc::now().to_rfc3339(),
        used_gib: memory_used,
        available_gib: memory_available,
        group_rss_gib: group_rss,
    }
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ring_buffer_inserts_in_order() {
        let mut buf = RingBuffer::new(5);
        buf.push(1);
        buf.push(2);
        buf.push(3);
        assert_eq!(buf.samples().len(), 3);
        assert!(buf.samples().len() < 5);
        let samples: Vec<&i32> = buf.samples().iter().collect();
        assert_eq!(samples, vec![&1, &2, &3]);
    }

    #[test]
    fn test_ring_buffer_drops_oldest_when_full() {
        let mut buf = RingBuffer::new(3);
        buf.push(1);
        buf.push(2);
        buf.push(3);
        assert_eq!(buf.samples().len(), 3);
        buf.push(4);
        assert_eq!(buf.samples().len(), 3);
        let samples: Vec<&i32> = buf.samples().iter().collect();
        assert_eq!(samples, vec![&2, &3, &4]);
    }

    #[test]
    fn test_ring_buffer_capacity_is_300() {
        let mut buf = RingBuffer::<i32>::default();
        for i in 0..350 {
            buf.push(i);
        }
        assert_eq!(buf.samples().len(), 300);
        // Oldest 50 should be dropped
        assert_eq!(*buf.samples().first().unwrap(), 50);
        assert_eq!(*buf.samples().last().unwrap(), 349);
    }

    #[test]
    fn test_ring_buffer_preserves_order() {
        let mut buf = RingBuffer::new(10);
        for i in 1..=10 {
            buf.push(i);
        }
        let values: Vec<&i32> = buf.samples().iter().collect();
        assert_eq!(values, vec![&1, &2, &3, &4, &5, &6, &7, &8, &9, &10]);
    }

    #[test]
    fn test_history_sample() {
        let mut groups = std::collections::HashMap::new();
        groups.insert(
            "pi".to_string(),
            crate::stats::Group {
                rss_gib: 1.5,
                cpu: 0.2,
                pids: vec![100],
            },
        );
        groups.insert(
            "hermes".to_string(),
            crate::stats::Group {
                rss_gib: 2.0,
                cpu: 0.5,
                pids: vec![200],
            },
        );
        groups.insert(
            "firefox".to_string(),
            crate::stats::Group {
                rss_gib: 3.0,
                cpu: 1.0,
                pids: vec![300],
            },
        );
        groups.insert(
            "system".to_string(),
            crate::stats::Group {
                rss_gib: 0.5,
                cpu: 0.1,
                pids: vec![400],
            },
        );
        groups.insert(
            "other".to_string(),
            crate::stats::Group {
                rss_gib: 1.0,
                cpu: 0.3,
                pids: vec![500],
            },
        );

        let sample = sample_from_stats(4.2, 7.8, &groups);
        assert_eq!(sample.used_gib, 4.2);
        assert_eq!(sample.available_gib, 7.8);
        assert_eq!(sample.group_rss_gib.len(), 5);
        assert_eq!(sample.group_rss_gib[0], 1.5); // pi
        assert_eq!(sample.group_rss_gib[1], 2.0); // hermes
        assert_eq!(sample.group_rss_gib[2], 3.0); // firefox
        assert_eq!(sample.group_rss_gib[3], 0.5); // system
        assert_eq!(sample.group_rss_gib[4], 1.0); // other
        assert!(!sample.timestamp.is_empty());
    }

    #[test]
    fn test_history_sample_missing_groups() {
        let groups = std::collections::HashMap::new();
        let sample = sample_from_stats(4.2, 7.8, &groups);
        assert_eq!(sample.group_rss_gib.len(), 5);
        assert_eq!(sample.group_rss_gib, vec![0.0, 0.0, 0.0, 0.0, 0.0]);
    }
}
