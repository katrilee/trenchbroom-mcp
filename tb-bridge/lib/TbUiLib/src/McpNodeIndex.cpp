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

#include "ui/McpNodeIndex.h"

#include "mdl/Brush.h"
#include "mdl/BrushFace.h"
#include "mdl/BrushNode.h"
#include "mdl/Entity.h"
#include "mdl/EntityNodeBase.h"
#include "mdl/GroupNode.h"
#include "mdl/Map.h"
#include "mdl/Node.h"
#include "mdl/WorldNode.h"
#include "ui/MapDocument.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <functional>

namespace tb::ui
{

namespace
{

class Hasher
{
private:
  uint64_t m_state = 1469598103934665603ULL;

public:
  void addByte(unsigned char byte)
  {
    m_state ^= static_cast<uint64_t>(byte);
    m_state *= 1099511628211ULL;
  }

  void add(const std::string& text)
  {
    for (const char c : text)
    {
      addByte(static_cast<unsigned char>(c));
    }
    addByte(0xff);
  }

  void add(double value)
  {
    // 1/16 unit quantisation: CSG float noise must not change an id, real
    // geometry changes must.
    const auto quantized = static_cast<int64_t>(std::llround(value * 16.0));
    for (int shift = 56; shift >= 0; shift -= 8)
    {
      addByte(static_cast<unsigned char>((quantized >> shift) & 0xff));
    }
  }

  uint64_t value() const { return m_state; }
};

std::string encode(uint64_t value)
{
  static constexpr char digits[] = "0123456789abcdefghijklmnopqrstuvwxyz";
  if (value == 0)
  {
    return "0";
  }
  auto buffer = std::string{};
  while (value > 0)
  {
    buffer.push_back(digits[value % 36]);
    value /= 36;
  }
  std::reverse(buffer.begin(), buffer.end());
  return buffer;
}

std::string fingerprintBrush(const mdl::Brush& brush)
{
  auto hasher = Hasher{};

  auto vertices = std::vector<vm::vec3d>{};
  for (const auto* vertex : brush.vertices())
  {
    vertices.push_back(vertex->position());
  }
  std::sort(vertices.begin(), vertices.end(), [](const auto& lhs, const auto& rhs) {
    if (lhs.x() != rhs.x())
    {
      return lhs.x() < rhs.x();
    }
    if (lhs.y() != rhs.y())
    {
      return lhs.y() < rhs.y();
    }
    return lhs.z() < rhs.z();
  });

  hasher.add("verts");
  for (const auto& vertex : vertices)
  {
    hasher.add(vertex.x());
    hasher.add(vertex.y());
    hasher.add(vertex.z());
  }

  hasher.add("faces");
  for (const auto& face : brush.faces())
  {
    hasher.add(face.attributes().materialName());
    const auto& normal = face.normal();
    hasher.add(normal.x());
    hasher.add(normal.y());
    hasher.add(normal.z());
  }

  return encode(hasher.value());
}

std::string fingerprintEntity(const mdl::Entity& entity)
{
  auto hasher = Hasher{};
  auto properties = entity.properties();
  std::sort(properties.begin(), properties.end(), [](const auto& lhs, const auto& rhs) {
    return lhs.key() < rhs.key();
  });
  for (const auto& property : properties)
  {
    hasher.add(property.key());
    hasher.add(property.value());
  }
  return encode(hasher.value());
}

std::string fingerprintNode(const mdl::Node& node)
{
  if (const auto* entityNode = dynamic_cast<const mdl::EntityNodeBase*>(&node))
  {
    return "e:" + fingerprintEntity(entityNode->entity());
  }
  if (const auto* brushNode = dynamic_cast<const mdl::BrushNode*>(&node))
  {
    return "b:" + fingerprintBrush(brushNode->brush());
  }
  if (dynamic_cast<const mdl::GroupNode*>(&node) != nullptr)
  {
    auto hasher = Hasher{};
    hasher.add("group");
    for (const auto* child : node.children())
    {
      hasher.add(fingerprintNode(*child));
    }
    return "g:" + encode(hasher.value());
  }
  return "x:" + encode(std::hash<const mdl::Node*>{}(&node));
}

} // namespace

void McpNodeIndex::rebuild(MapDocument& document)
{
  clear();

  auto& world = document.map().worldNode();
  auto counters = std::unordered_map<std::string, int>{};

  const auto visit = [&](mdl::Node* node, auto&& self) -> void {
    const auto base = fingerprintNode(*node);
    auto& ordinal = counters[base];
    ++ordinal;

    auto id = ordinal == 1 ? base : base + "-" + std::to_string(ordinal);
    m_byId.emplace(id, node);
    m_byNode.emplace(node, std::move(id));

    for (auto* child : node->children())
    {
      self(child, self);
    }
  };

  for (auto* child : world.children())
  {
    visit(child, visit);
  }
  m_byNode.emplace(&world, "w:world");
  m_byId.emplace("w:world", &world);
}

void McpNodeIndex::clear()
{
  m_byId.clear();
  m_byNode.clear();
}

const std::string& McpNodeIndex::idOf(const mdl::Node* node) const
{
  static const auto empty = std::string{};
  if (node == nullptr)
  {
    return empty;
  }
  const auto it = m_byNode.find(node);
  return it == m_byNode.end() ? empty : it->second;
}

mdl::Node* McpNodeIndex::find(const std::string& id) const
{
  const auto it = m_byId.find(id);
  return it == m_byId.end() ? nullptr : it->second;
}

std::vector<mdl::Node*> McpNodeIndex::resolve(
  const std::vector<std::string>& ids, std::vector<std::string>& missing) const
{
  auto result = std::vector<mdl::Node*>{};
  result.reserve(ids.size());
  for (const auto& id : ids)
  {
    if (auto* node = find(id))
    {
      result.push_back(node);
    }
    else
    {
      missing.push_back(id);
    }
  }
  return result;
}

} // namespace tb::ui
