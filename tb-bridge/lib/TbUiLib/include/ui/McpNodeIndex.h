/*
 Copyright (C) 2026 TrenchBroom MCP Bridge contributors

 This file is part of TrenchBroom.

 TrenchBroom is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.

 TrenchBroom is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.

 You should have received a copy of the GNU General Public License
 along with TrenchBroom. If not, see <http://www.gnu.org/licenses/>.
 */

#pragma once

#include <string>
#include <unordered_map>
#include <vector>

namespace tb::mdl
{
class Node;
} // namespace tb::mdl

namespace tb::ui
{
class MapDocument;

/**
 * Stable string identifiers for document nodes.
 *
 * `mdl::Node` carries no persistent id, and commands (including undo) destroy
 * and recreate node objects, so raw pointers cannot be handed to an external
 * client. The index derives ids from content instead:
 *
 *   entity : e:<fnv1a64(properties sorted)>
 *   brush  : b:<fnv1a64(1/16-quantised vertices + face material names)>
 *   group  : g:<fnv1a64(descendant fingerprints)>
 *   world  : w:world
 *
 * Identical siblings get `-2`, `-3` ordinals. Ids survive undo/redo and
 * save/reload; a destructive edit can retire one, and clients must handle
 * `id_not_found` rather than assume the node still exists.
 */
class McpNodeIndex
{
private:
  std::unordered_map<std::string, mdl::Node*> m_byId;
  std::unordered_map<const mdl::Node*, std::string> m_byNode;

public:
  McpNodeIndex() = default;

  void rebuild(MapDocument& document);
  void clear();

  const std::string& idOf(const mdl::Node* node) const;
  mdl::Node* find(const std::string& id) const;

  std::vector<mdl::Node*> resolve(
    const std::vector<std::string>& ids, std::vector<std::string>& missing) const;

  size_t size() const { return m_byId.size(); }
};

} // namespace tb::ui
