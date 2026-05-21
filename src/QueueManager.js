'use strict';

/**
 * Two presets:
 *
 * fifo        – Pure chronological order. Songs play in the exact order they
 *               were added. One singer can queue many songs in a row; the order
 *               is never reshuffled. Hard per-singer limit enforced.
 *
 * smart_queue – Fair round-robin. Every new singer is pushed above existing
 *               singers' later songs so no one ever sings two in a row while
 *               others are waiting. Singer with fewest songs always goes first.
 */

class QueueManager {
  /** @param {object} db - src/database.js exports */
  constructor(db) {
    this._db = db;
    /** @type {Map<string, number>}  singerName → expiry ms */
    this._cooldowns = new Map();
  }

  // ── Config ──────────────────────────────────────────────────────────────────

  getConfig() {
    const s = this._db.getSettings();
    let preset = s.queue_preset || 'smart_queue';
    // Migrate old preset names
    if (preset === 'strictly_fair') preset = 'fifo';
    if (['inclusive_jam', 'party_flow', 'custom'].includes(preset)) preset = 'smart_queue';
    return {
      preset,
      maxSongs:    parseInt(s.request_song_limit || '3') || 3,
      cooldownSec: parseInt(s.queue_cooldown_sec  || '0') || 0,
    };
  }

  // ── Admission ───────────────────────────────────────────────────────────────

  /**
   * Returns { allowed: true } or { allowed: false, reason, …details }.
   * Call before addToQueue.
   */
  validateAdmission(singerName) {
    if (!singerName?.trim()) return { allowed: false, reason: 'no_name' };
    const name = singerName.trim();
    const cfg  = this.getConfig();

    // Cooldown
    const remaining = this.cooldownRemaining(name);
    if (remaining > 0) {
      return { allowed: false, reason: 'cooldown', remainSec: remaining };
    }

    const currentCount = this._db.getUserQueueCount(name);
    if (currentCount >= cfg.maxSongs) {
      if (cfg.preset === 'smart_queue') {
        // In Smart Queue: block only if another singer has fewer songs waiting
        const othersWithFewer = this._db.singersWithFewerSongs(name, currentCount);
        if (othersWithFewer > 0) {
          return {
            allowed: false,
            reason:  'smart_queue',
            message: 'Let others take a turn first',
          };
        }
        return { allowed: true }; // nobody else waiting — allow
      }
      // fifo: hard block
      return { allowed: false, reason: 'limit', count: currentCount, max: cfg.maxSongs };
    }

    return { allowed: true };
  }

  // ── Recalculate ─────────────────────────────────────────────────────────────

  /**
   * Re-sorts all pending (unpinned) entries.
   * Pinned entries keep their positions; free entries fill the gaps.
   */
  recalculateQueue() {
    const entries = this._db.getQueueWithTiming();
    if (entries.length <= 1) return;

    const cfg     = this.getConfig();
    const pinned  = entries.filter(e => e.pinned);
    const free    = entries.filter(e => !e.pinned);

    // Know who is currently on stage so they don't get the next slot too
    const current       = this._db.getCurrentSong();
    const currentSinger = current?.singer_name || null;

    const sorted = cfg.preset === 'smart_queue'
      ? this._sortSmartQueue(free, currentSinger)
      : this._sortFifo(free);

    // Merge: pinned hold their positions, sorted free fill the gaps
    const total  = entries.length;
    const result = new Array(total).fill(null);

    for (const p of pinned) {
      result[Math.max(0, Math.min(p.position, total - 1))] = p;
    }
    let fi = 0;
    for (let i = 0; i < total; i++) {
      if (!result[i]) result[i] = sorted[fi++];
    }

    const newOrder = result.map(e => e.id);
    const oldOrder = entries.map(e => e.id);

    // Skip the DB write (and disk flush) if nothing changed
    const changed = newOrder.some((id, i) => id !== oldOrder[i]);
    if (changed) this._db.reorderQueue(newOrder);
    return changed;
  }

  // ── Sorting strategies ──────────────────────────────────────────────────────

  /** FIFO: strict insertion order (oldest first). */
  _sortFifo(entries) {
    return [...entries].sort((a, b) => b.wait_sec - a.wait_sec);
  }

  /**
   * Smart Queue: greedy no-consecutive algorithm.
   *
   * At every slot, pick the highest-priority singer who is NOT the previous
   * slot's singer (including whoever is currently on stage).
   * Only falls back to the same singer if no alternative exists.
   *
   * Priority = fewest remaining songs; ties broken by longest wait time.
   *
   * @param {object[]} entries
   * @param {string|null} currentSinger  - singer currently playing (occupies virtual slot -1)
   */
  _sortSmartQueue(entries, currentSinger = null) {
    if (!entries.length) return [];

    // Group by singer, each group sorted oldest-first
    const pool = {};
    for (const e of entries) {
      const n = e.singer_name || '';
      (pool[n] = pool[n] || []).push(e);
    }
    for (const n of Object.keys(pool)) {
      pool[n].sort((a, b) => b.wait_sec - a.wait_sec);
    }

    // Lower value = higher priority
    const priority = name => {
      const songs = pool[name];
      if (!songs?.length) return Infinity;
      return songs.length * 1_000_000 - songs[0].wait_sec;
    };

    const result     = [];
    let   lastSinger = currentSinger; // the "previous slot" starts with whoever is on stage

    while (Object.keys(pool).length) {
      const allSingers  = Object.keys(pool);
      const alternatives = allSingers.filter(n => n !== lastSinger);

      // Prefer any singer other than the last one; fall back only if no choice
      const candidates = alternatives.length ? alternatives : allSingers;
      const chosen     = candidates.reduce((best, n) =>
        priority(n) < priority(best) ? n : best
      );

      result.push(pool[chosen].shift());
      if (!pool[chosen].length) delete pool[chosen];
      lastSinger = chosen;
    }

    return result;
  }

  // ── Cooldown ────────────────────────────────────────────────────────────────

  applyCooldown(singerName) {
    if (!singerName) return;
    const { cooldownSec } = this.getConfig();
    if (cooldownSec <= 0) return;
    this._cooldowns.set(singerName.trim(), Date.now() + cooldownSec * 1_000);
  }

  cooldownRemaining(singerName) {
    const expiry = this._cooldowns.get(singerName?.trim() || '');
    if (!expiry || Date.now() >= expiry) return 0;
    return Math.ceil((expiry - Date.now()) / 1_000);
  }

  _isInCooldown(singerName) { return this.cooldownRemaining(singerName) > 0; }

  purgeCooldowns() {
    const now = Date.now();
    for (const [n, exp] of this._cooldowns) {
      if (now >= exp) this._cooldowns.delete(n);
    }
  }

  status() {
    const cfg = this.getConfig();
    const cooldowns = [];
    const now = Date.now();
    for (const [name, exp] of this._cooldowns) {
      if (exp > now) cooldowns.push({ name, remainSec: Math.ceil((exp - now) / 1_000) });
    }
    return { ...cfg, cooldowns };
  }
}

module.exports = QueueManager;
