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

#include "ui/McpServerBridge.h"

#include "Error.h"
#include "Result.h"

#include "gl/Camera.h"
#include "gl/GlManager.h"
#include "gl/Material.h"
#include "gl/MaterialManager.h"
#include "gl/Texture.h"

#include "mdl/Brush.h"
#include "mdl/BrushBuilder.h"
#include "mdl/BrushFace.h"
#include "mdl/BrushFaceAttributes.h"
#include "mdl/BrushFaceHandle.h"
#include "mdl/BrushNode.h"
#include "mdl/Entity.h"
#include "mdl/EntityDefinition.h"
#include "mdl/EntityDefinitionFileSpec.h"
#include "mdl/EntityDefinitionManager.h"
#include "mdl/EntityNode.h"
#include "mdl/EntityNodeBase.h"
#include "mdl/EditorContext.h"
#include "mdl/EntityProperties.h"
#include "mdl/GameInfo.h"
#include "mdl/GameManager.h"
#include "mdl/Grid.h"
#include "mdl/GroupNode.h"
#include "mdl/Issue.h"
#include "mdl/Layer.h"
#include "mdl/LayerNode.h"
#include "mdl/Map.h"
#include "mdl/Map_Assets.h"
#include "mdl/Map_Brushes.h"
#include "mdl/Map_CopyPaste.h"
#include "mdl/Map_Entities.h"
#include "mdl/Map_Geometry.h"
#include "mdl/Map_Groups.h"
#include "mdl/Map_Layers.h"
#include "mdl/Map_Nodes.h"
#include "mdl/Map_NodeVisibility.h"
#include "mdl/Map_Selection.h"
#include "mdl/Map_World.h"
#include "mdl/UVCoordSystem.h"
#include "mdl/MapFormat.h"
#include "mdl/Node.h"
#include "mdl/Transaction.h"
#include "mdl/UpdateBrushFaceAttributes.h"
#include "mdl/WorldNode.h"

#include "PreferenceManager.h"
#include "Preferences.h"
#include "vm/mat.h"

#include "ui/AppController.h"
#include "ui/MapDocument.h"
#include "ui/MapView2D.h"
#include "ui/MapView3D.h"
#include "ui/MapViewLayout.h"
#include "ui/MapWindow.h"
#include "ui/MapWindowManager.h"
#include "ui/McpJson.h"
#include "ui/SwitchableMapViewContainer.h"

#include <QBuffer>
#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QImage>
#include <QJsonArray>
#include <QJsonDocument>
#include <QMetaObject>
#include <QPainter>
#include <QPen>
#include <QString>

#include <set>
#include <QTcpSocket>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <exception>
#include <variant>

namespace tb::ui
{

namespace
{

constexpr double Pi = 3.14159265358979323846;

template <typename T>
std::string resultError(const Result<T>& result)
{
  return std::get<Error>(result.error()).msg;
}

std::string mapFormatName(const mdl::MapFormat format)
{
  switch (format)
  {
  case mdl::MapFormat::Standard:
    return "standard";
  case mdl::MapFormat::Quake2:
    return "quake2";
  case mdl::MapFormat::Quake2_Valve:
    return "quake2_valve";
  case mdl::MapFormat::Valve:
    return "valve";
  case mdl::MapFormat::Hexen2:
    return "hexen2";
  case mdl::MapFormat::Daikatana:
    return "daikatana";
  default:
    return "unknown";
  }
}

bool isPowerOfTwo(int value)
{
  return value > 0 && (value & (value - 1)) == 0;
}

vm::vec3d snapVector(const vm::vec3d& value, const int grid)
{
  if (grid <= 1)
  {
    return value;
  }
  const auto step = static_cast<double>(grid);
  return vm::vec3d{
    std::round(value.x() / step) * step,
    std::round(value.y() / step) * step,
    std::round(value.z() / step) * step};
}

enum class FaceFilter
{
  All,
  Floor,
  Ceiling,
  Walls,
  PlusX,
  MinusX,
  PlusY,
  MinusY,
};

FaceFilter parseFaceFilter(const std::string& text)
{
  if (text == "floor" || text == "+z" || text == "up")
  {
    return FaceFilter::Floor;
  }
  if (text == "ceiling" || text == "-z" || text == "down")
  {
    return FaceFilter::Ceiling;
  }
  if (text == "walls" || text == "wall" || text == "sides")
  {
    return FaceFilter::Walls;
  }
  if (text == "+x")
  {
    return FaceFilter::PlusX;
  }
  if (text == "-x")
  {
    return FaceFilter::MinusX;
  }
  if (text == "+y")
  {
    return FaceFilter::PlusY;
  }
  if (text == "-y")
  {
    return FaceFilter::MinusY;
  }
  return FaceFilter::All;
}

bool isAxis(const vm::vec3d& v, const double x, const double y, const double z)
{
  return std::abs(v.x() - x) < 1e-3 && std::abs(v.y() - y) < 1e-3
         && std::abs(v.z() - z) < 1e-3;
}

bool faceMatches(const mdl::BrushFace& face, const FaceFilter filter)
{
  const auto& normal = face.normal();
  switch (filter)
  {
  case FaceFilter::All:
    return true;
  case FaceFilter::Floor:
    return isAxis(normal, 0, 0, 1);
  case FaceFilter::Ceiling:
    return isAxis(normal, 0, 0, -1);
  case FaceFilter::Walls:
    return std::abs(normal.z()) < 1e-3;
  case FaceFilter::PlusX:
    return isAxis(normal, 1, 0, 0);
  case FaceFilter::MinusX:
    return isAxis(normal, -1, 0, 0);
  case FaceFilter::PlusY:
    return isAxis(normal, 0, 1, 0);
  case FaceFilter::MinusY:
    return isAxis(normal, 0, -1, 0);
  }
  return true;
}

QJsonArray jsonFromStringVector(const std::vector<std::string>& values)
{
  auto array = QJsonArray{};
  for (const auto& value : values)
  {
    array.append(QString::fromStdString(value));
  }
  return array;
}

} // namespace

// ---------------------------------------------------------------------------
// construction / lifecycle
// ---------------------------------------------------------------------------

McpServerBridge::McpServerBridge(MapWindow* window, QObject* parent)
  : QObject{parent}
  , m_window{window}
{
  registerHandlers();

  if (const auto* token = std::getenv("TB_MCP_TOKEN"))
  {
    m_authToken = token;
  }

  instanceRef() = this;

  connect(&m_server, &QTcpServer::newConnection, this, &McpServerBridge::onNewConnection);
  connect(
    &m_watcher,
    &QFileSystemWatcher::fileChanged,
    this,
    &McpServerBridge::onWatchedFileChanged);
}

McpServerBridge::~McpServerBridge()
{
  stop();
  if (instanceRef() == this)
  {
    instanceRef() = nullptr;
  }
}

bool McpServerBridge::start(const quint16 port)
{
  if (m_server.isListening())
  {
    return true;
  }
  return m_server.listen(QHostAddress{QHostAddress::LocalHost}, port);
}

void McpServerBridge::stop()
{
  for (auto& connection : m_connections)
  {
    if (connection->socket != nullptr)
    {
      connection->socket->disconnect(this);
      connection->socket->abort();
      connection->socket->deleteLater();
      connection->socket = nullptr;
    }
  }
  m_connections.clear();
  if (m_server.isListening())
  {
    m_server.close();
  }
}

bool McpServerBridge::isListening() const
{
  return m_server.isListening();
}

quint16 McpServerBridge::port() const
{
  return m_server.serverPort();
}

void McpServerBridge::setAuthToken(std::string token)
{
  m_authToken = std::move(token);
}

QJsonObject McpServerBridge::info() const
{
  auto result = QJsonObject{};
  result.insert(QLatin1String{"protocolVersion"}, ProtocolVersion);
  result.insert(QLatin1String{"trenchbroomVersion"}, QLatin1String{"2026.2"});
  result.insert(QLatin1String{"listening"}, m_server.isListening());
  result.insert(QLatin1String{"port"}, static_cast<int>(m_server.serverPort()));
  result.insert(QLatin1String{"authRequired"}, !m_authToken.empty());
  result.insert(QLatin1String{"pid"}, static_cast<double>(QCoreApplication::applicationPid()));

  auto methods = QJsonArray{};
  for (const auto& [name, handler] : m_handlers)
  {
    methods.append(QString::fromStdString(name));
  }
  result.insert(QLatin1String{"methods"}, methods);
  return result;
}

McpServerBridge*& McpServerBridge::instanceRef()
{
  static McpServerBridge* bridge = nullptr;
  return bridge;
}

McpServerBridge* McpServerBridge::instance()
{
  return instanceRef();
}

McpServerBridge* McpServerBridge::attachTo(MapWindow& window)
{
  if (const auto* enabled = std::getenv("TB_MCP_ENABLE"))
  {
    if (std::string{enabled} == "0")
    {
      return nullptr;
    }
  }

  auto port = static_cast<quint16>(DefaultPort);
  if (const auto* portEnv = std::getenv("TB_MCP_PORT"))
  {
    const auto parsed = std::atoi(portEnv);
    if (parsed > 0)
    {
      port = static_cast<quint16>(parsed);
    }
  }

  if (auto* existing = instance(); existing != nullptr)
  {
    existing->m_window = &window;
    if (!existing->isListening())
    {
      existing->start(port);
    }
    return existing;
  }

  auto* bridge = new McpServerBridge{&window, &window};
  if (!bridge->start(port))
  {
    bridge->deleteLater();
    return nullptr;
  }
  return bridge;
}

McpServerBridge* McpServerBridge::startGlobal(QObject* parent)
{
  if (const auto* enabled = std::getenv("TB_MCP_ENABLE"))
  {
    if (std::string{enabled} == "0")
    {
      return nullptr;
    }
  }

  if (auto* existing = instance(); existing != nullptr)
  {
    return existing;
  }

  auto port = static_cast<quint16>(DefaultPort);
  if (const auto* portEnv = std::getenv("TB_MCP_PORT"))
  {
    const auto parsed = std::atoi(portEnv);
    if (parsed > 0)
    {
      port = static_cast<quint16>(parsed);
    }
  }

  auto* bridge = new McpServerBridge{nullptr, parent};
  if (!bridge->start(port))
  {
    bridge->deleteLater();
    return nullptr;
  }
  return bridge;
}

// ---------------------------------------------------------------------------
// socket plumbing
// ---------------------------------------------------------------------------

void McpServerBridge::onNewConnection()
{
  while (auto* socket = m_server.nextPendingConnection())
  {
    auto connection = std::make_unique<Connection>();
    connection->socket = socket;

    connect(socket, &QTcpSocket::readyRead, this, &McpServerBridge::onReadyRead);
    connect(
      socket, &QTcpSocket::disconnected, this, &McpServerBridge::onSocketDisconnected);

    m_connections.push_back(std::move(connection));
  }
}

void McpServerBridge::onReadyRead()
{
  auto* socket = qobject_cast<QTcpSocket*>(sender());
  if (socket == nullptr)
  {
    return;
  }

  auto* connection = static_cast<Connection*>(nullptr);
  for (auto& candidate : m_connections)
  {
    if (candidate->socket == socket)
    {
      connection = candidate.get();
      break;
    }
  }
  if (connection == nullptr)
  {
    return;
  }

  connection->buffer.append(socket->readAll());

  auto start = qsizetype{0};
  while (true)
  {
    const auto newline = connection->buffer.indexOf('\n', start);
    if (newline < 0)
    {
      break;
    }
    const auto line = connection->buffer.mid(start, newline - start).trimmed();
    if (!line.isEmpty())
    {
      connection->pending.push_back(line);
    }
    start = newline + 1;
  }
  connection->buffer.remove(0, start);

  if (connection->pending.empty() || connection->scheduled)
  {
    return;
  }

  connection->scheduled = true;
  auto* raw = connection;
  QMetaObject::invokeMethod(
    this,
    [this, raw]() {
      raw->scheduled = false;
      while (!raw->pending.empty())
      {
        const auto line = raw->pending.front();
        raw->pending.pop_front();
        if (raw->socket != nullptr)
        {
          handleLine(*raw, line);
        }
      }
    },
    Qt::QueuedConnection);
}

void McpServerBridge::onSocketDisconnected()
{
  auto* socket = qobject_cast<QTcpSocket*>(sender());
  if (socket == nullptr)
  {
    return;
  }

  m_connections.erase(
    std::remove_if(
      m_connections.begin(),
      m_connections.end(),
      [&](const auto& connection) {
        if (connection->socket == socket)
        {
          connection->socket = nullptr;
          return true;
        }
        return false;
      }),
    m_connections.end());

  socket->deleteLater();
}

void McpServerBridge::handleLine(Connection& connection, const QByteArray& line)
{
  auto parseError = QJsonParseError{};
  const auto parsed = QJsonDocument::fromJson(line, &parseError);
  if (parsed.isNull() || !parsed.isObject())
  {
    auto response = QJsonObject{};
    response.insert(QLatin1String{"jsonrpc"}, QLatin1String{"2.0"});
    response.insert(QLatin1String{"id"}, QJsonValue::Null);
    response.insert(
      QLatin1String{"error"},
      mcp::makeError(
        mcp::ErrorCode::ParseError,
        "invalid JSON: " + parseError.errorString().toStdString()));
    send(connection, response);
    return;
  }

  send(connection, execute(parsed.object()));
}

QJsonObject McpServerBridge::execute(const QJsonObject& request)
{
  auto response = QJsonObject{};
  response.insert(QLatin1String{"jsonrpc"}, QLatin1String{"2.0"});

  const auto idValue = request.value(QLatin1String{"id"});
  response.insert(QLatin1String{"id"}, idValue);

  const auto method = request.value(QLatin1String{"method"}).toString().toStdString();
  if (method.empty())
  {
    response.insert(
      QLatin1String{"error"},
      mcp::makeError(mcp::ErrorCode::InvalidRequest, "missing method"));
    return response;
  }

  if (!m_authToken.empty())
  {
    const auto supplied = request.value(QLatin1String{"auth"}).toString().toStdString();
    if (supplied != m_authToken)
    {
      response.insert(
        QLatin1String{"error"},
        mcp::makeError(mcp::ErrorCode::InvalidRequest, "auth token missing or invalid"));
      return response;
    }
  }

  const auto params = request.value(QLatin1String{"params"}).toObject();

  try
  {
    if (method == "rpc.ping" || method == "rpc.methods")
    {
      response.insert(QLatin1String{"result"}, info());
      return response;
    }

    const auto handlerIt = m_handlers.find(method);
    if (handlerIt == m_handlers.end())
    {
      response.insert(
        QLatin1String{"error"},
        mcp::makeError(mcp::ErrorCode::MethodNotFound, "unknown method: " + method));
      return response;
    }

    if (m_window != nullptr)
    {
      m_index.rebuild(document());
    }
    response.insert(QLatin1String{"result"}, handlerIt->second(params));
  }
  catch (const McpError& error)
  {
    response.insert(QLatin1String{"error"}, mcp::makeError(error.code, error.message));
  }
  catch (const std::exception& exception)
  {
    response.insert(
      QLatin1String{"error"},
      mcp::makeError(mcp::ErrorCode::InternalError, exception.what()));
  }
  catch (...)
  {
    response.insert(
      QLatin1String{"error"},
      mcp::makeError(mcp::ErrorCode::InternalError, "unknown C++ exception"));
  }

  return response;
}

void McpServerBridge::send(Connection& connection, const QJsonObject& response)
{
  if (connection.socket == nullptr || response.value(QLatin1String{"id"}).isUndefined())
  {
    return;
  }
  const auto payload = QJsonDocument{response}.toJson(QJsonDocument::Compact) + '\n';
  connection.socket->write(payload);
  connection.socket->flush();
}

// ---------------------------------------------------------------------------
// accessors
// ---------------------------------------------------------------------------

MapWindow& McpServerBridge::requireWindow() const
{
  if (m_window == nullptr)
  {
    throw McpError{mcp::ErrorCode::BadState, "bridge is not attached to a MapWindow"};
  }
  return *m_window;
}

MapDocument& McpServerBridge::document() const
{
  if (m_window == nullptr)
  {
    throw McpError{
      mcp::ErrorCode::NoDocument,
      "no document is open; call doc.new_or_open first"};
  }
  return requireWindow().document();
}

mdl::Map& McpServerBridge::map() const
{
  return document().map();
}

std::vector<std::string> McpServerBridge::stampIds(const std::vector<mdl::Node*>& nodes)
{
  m_index.rebuild(document());
  auto ids = std::vector<std::string>{};
  ids.reserve(nodes.size());
  for (auto* node : nodes)
  {
    ids.push_back(m_index.idOf(node));
  }
  return ids;
}

std::vector<mdl::Node*> McpServerBridge::addToCurrentParent(
  const std::vector<mdl::Node*>& nodes)
{
  auto& m = map();
  auto toAdd = std::map<mdl::Node*, std::vector<mdl::Node*>>{};
  toAdd[mdl::parentForNodes(m)] = nodes;
  return mdl::addNodes(m, toAdd);
}

void McpServerBridge::selectOnly(const std::vector<mdl::Node*>& nodes)
{
  auto& m = map();
  mdl::deselectAll(m);
  if (!nodes.empty())
  {
    mdl::selectNodes(m, nodes);
  }
}

QJsonObject McpServerBridge::nodeSummary(mdl::Node* node) const
{
  auto summary = QJsonObject{};
  summary.insert(QLatin1String{"id"}, QString::fromStdString(m_index.idOf(node)));
  summary.insert(QLatin1String{"bounds"}, mcp::bboxToJson(node->logicalBounds()));

  if (auto* entityNode = dynamic_cast<mdl::EntityNode*>(node))
  {
    summary.insert(QLatin1String{"type"}, QLatin1String{"entity"});
    const auto* classname =
      entityNode->entity().property(mdl::EntityPropertyKeys::Classname);
    summary.insert(
      QLatin1String{"classname"},
      classname != nullptr ? QString::fromStdString(*classname) : QString{});
    const auto* origin = entityNode->entity().property(mdl::EntityPropertyKeys::Origin);
    summary.insert(
      QLatin1String{"origin"},
      origin != nullptr ? QString::fromStdString(*origin) : QString{});
  }
  else if (auto* brushNode = dynamic_cast<mdl::BrushNode*>(node))
  {
    summary.insert(QLatin1String{"type"}, QLatin1String{"brush"});
    summary.insert(
      QLatin1String{"faceCount"}, static_cast<int>(brushNode->brush().faceCount()));
    summary.insert(
      QLatin1String{"material"},
      QString::fromStdString(brushNode->brush().face(0).attributes().materialName()));
  }
  else if (dynamic_cast<mdl::GroupNode*>(node) != nullptr)
  {
    summary.insert(QLatin1String{"type"}, QLatin1String{"group"});
  }
  else if (dynamic_cast<mdl::LayerNode*>(node) != nullptr)
  {
    summary.insert(QLatin1String{"type"}, QLatin1String{"layer"});
  }
  else
  {
    summary.insert(QLatin1String{"type"}, QLatin1String{"node"});
  }
  return summary;
}

// ---------------------------------------------------------------------------
// handler registration
// ---------------------------------------------------------------------------

void McpServerBridge::registerHandlers()
{
  m_handlers["doc.new_or_open"] = [this](const auto& p) { return docNewOrOpen(p); };
  m_handlers["doc.state"] = [this](const auto& p) { return docState(p); };
  m_handlers["doc.info"] = [this](const auto& p) { return docState(p); };
  m_handlers["doc.save_map"] = [this](const auto& p) { return docSave(p); };
  m_handlers["doc.undo"] = [this](const auto& p) { return docUndo(p); };
  m_handlers["doc.redo"] = [this](const auto& p) { return docRedo(p); };
  m_handlers["doc.clear_selection"] = [this](const auto& p) { return docClearSelection(p); };
  m_handlers["doc.select_by_id"] = [this](const auto& p) { return docSelectById(p); };
  m_handlers["doc.select_by_bbox"] = [this](const auto& p) { return docSelectByBBox(p); };
  m_handlers["doc.select_all"] = [this](const auto& p) { return docSelectAll(p); };
  m_handlers["doc.selection"] = [this](const auto& p) { return docSelection(p); };
  m_handlers["doc.list_nodes"] = [this](const auto& p) { return docListNodeIds(p); };
  m_handlers["doc.set_mods"] = [this](const auto& p) { return docSetMods(p); };

  m_handlers["geo.create_cuboid"] = [this](const auto& p) { return geoCreateCuboid(p); };
  m_handlers["geo.create_brush_from_points"] = [this](const auto& p) { return geoCreateBrushFromPoints(p); };
  m_handlers["geo.create_room_hollow"] = [this](const auto& p) { return geoCreateRoomHollow(p); };
  m_handlers["geo.csg_subtract"] = [this](const auto& p) { return geoCsgSubtract(p); };
  m_handlers["geo.csg_union"] = [this](const auto& p) { return geoCsgUnion(p); };
  m_handlers["geo.csg_intersect"] = [this](const auto& p) { return geoCsgIntersect(p); };
  m_handlers["geo.clip_brush"] = [this](const auto& p) { return geoClipBrush(p); };
  m_handlers["geo.transform"] = [this](const auto& p) { return geoTransform(p); };
  m_handlers["geo.delete_selected"] = [this](const auto& p) { return geoDeleteSelected(p); };
  m_handlers["geo.duplicate_selected"] = [this](const auto& p) { return geoDuplicateSelected(p); };

  m_handlers["tex.load_wad"] = [this](const auto& p) { return matLoadCollection(p); };
  m_handlers["tex.list_textures"] = [this](const auto& p) { return matList(p); };
  m_handlers["tex.apply_to_brush_or_face"] = [this](const auto& p) { return matApply(p); };
  m_handlers["tex.set_properties"] = [this](const auto& p) { return matSetProperties(p); };

  m_handlers["ent.create_point_entity"] = [this](const auto& p) { return entCreatePoint(p); };
  m_handlers["ent.create_brush_entity"] = [this](const auto& p) { return entCreateBrush(p); };
  m_handlers["ent.set_property"] = [this](const auto& p) { return entSetProperty(p); };
  m_handlers["ent.list"] = [this](const auto& p) { return entList(p); };
  m_handlers["prefab.paste"] = [this](const auto& p) { return prefabPaste(p); };

  m_handlers["view.capture_3d"] = [this](const auto& p) { return viewCapture3D(p); };
  m_handlers["view.capture_orthographic"] = [this](const auto& p) { return viewCaptureOrtho(p); };
  m_handlers["view.load_pointfile"] = [this](const auto& p) { return viewLoadPointFile(p); };
  m_handlers["view.set_layout"] = [this](const auto& p) { return viewSetLayout(p); };
  m_handlers["view.camera_info"] = [this](const auto& p) { return viewCameraInfo(p); };

  m_handlers["val.get_issues"] = [this](const auto& p) { return valGetIssues(p); };

  m_handlers["layer.create"] = [this](const auto& p) { return layerCreate(p); };
  m_handlers["layer.set_active"] = [this](const auto& p) { return layerSetActive(p); };
  m_handlers["layer.list"] = [this](const auto& p) { return layerList(p); };
  m_handlers["group.create"] = [this](const auto& p) { return groupCreate(p); };
  m_handlers["group.ungroup"] = [this](const auto& p) { return groupUngroup(p); };

  m_handlers["geo.get_brush_topology"] = [this](const auto& p) { return geoGetBrushTopology(p); };
  m_handlers["geo.move_vertices"] = [this](const auto& p) { return geoMoveVertices(p); };
  m_handlers["geo.move_edges"] = [this](const auto& p) { return geoMoveEdges(p); };
  m_handlers["geo.move_faces"] = [this](const auto& p) { return geoMoveFaces(p); };
  m_handlers["geo.extrude_faces"] = [this](const auto& p) { return geoExtrudeFaces(p); };
  m_handlers["geo.add_vertex"] = [this](const auto& p) { return geoAddVertex(p); };
  m_handlers["geo.remove_vertices"] = [this](const auto& p) { return geoRemoveVertices(p); };
  m_handlers["geo.snap_vertices"] = [this](const auto& p) { return geoSnapVertices(p); };
  m_handlers["geo.shear"] = [this](const auto& p) { return geoShear(p); };
  m_handlers["geo.flip"] = [this](const auto& p) { return geoFlip(p); };
  m_handlers["geo.duplicate_and_transform"] = [this](const auto& p) { return geoDuplicateAndTransform(p); };

  m_handlers["tex.fit_texture"] = [this](const auto& p) { return texFitTexture(p); };
  m_handlers["tex.align_to_face"] = [this](const auto& p) { return texAlignToFace(p); };
  m_handlers["tex.align_to_world"] = [this](const auto& p) { return texAlignToWorld(p); };
  m_handlers["tex.copy_uv_between_faces"] = [this](const auto& p) { return texCopyUvBetweenFaces(p); };

  m_handlers["sel.select_touching"] = [this](const auto& p) { return selSelectTouching(p); };
  m_handlers["sel.select_inside"] = [this](const auto& p) { return selSelectInside(p); };

  m_handlers["view.isolate_selection"] = [this](const auto& p) { return viewIsolateSelection(p); };
  m_handlers["view.hide_selection"] = [this](const auto& p) { return viewHideSelection(p); };
  m_handlers["view.unhide_all"] = [this](const auto& p) { return viewUnhideAll(p); };
  m_handlers["view.set_render_mode"] = [this](const auto& p) { return viewSetRenderMode(p); };

  m_handlers["snapshot.save"] = [this](const auto& p) { return snapshotSave(p); };
  m_handlers["snapshot.list"] = [this](const auto& p) { return snapshotList(p); };
  m_handlers["snapshot.restore"] = [this](const auto& p) { return snapshotRestore(p); };
}

// ---------------------------------------------------------------------------
// document handlers
// ---------------------------------------------------------------------------

QJsonObject McpServerBridge::docNewOrOpen(const QJsonObject& p)
{
  auto& window = requireWindow();
  auto& app = window.appController();

  const auto gameName = mcp::stringFromJson(p.value(QLatin1String{"game"}), "Cstrike");
  const auto gamePath = mcp::stringFromJson(p.value(QLatin1String{"game_path"}));
  const auto mapPath = mcp::stringFromJson(p.value(QLatin1String{"map_path"}));

  const auto formatName = mcp::stringFromJson(p.value(QLatin1String{"map_format"}), "valve");
  const auto format = formatName == "standard" || formatName == "quake"
                        ? mdl::MapFormat::Standard
                        : mdl::MapFormat::Valve;

  auto& games = app.gameManager();
  auto* gameInfo = games.gameInfo(gameName);
  if (gameInfo == nullptr)
  {
    throw McpError{
      mcp::ErrorCode::OperationFailed,
      "unknown game \"" + gameName + "\"; loaded game configs do not contain it"};
  }

  // Reuse the window we already have. Creating a second MapWindow would stack
  // windows (and leave the old document alive), which is what an agent calling
  // doc.new_or_open repeatedly must never do.
  if (m_window != nullptr)
  {
    auto& current = document();
    const auto applied = mapPath.empty()
                           ? current.create(
                               app.environmentConfig(),
                               *gameInfo,
                               format,
                               MapDocument::DefaultWorldBounds)
                           : current.load(
                               app.environmentConfig(),
                               *gameInfo,
                               format,
                               MapDocument::DefaultWorldBounds,
                               std::filesystem::path{mapPath});
    if (applied.is_error())
    {
      throw McpError{
        mcp::ErrorCode::OperationFailed,
        "could not create/open document: " + resultError(applied)};
    }
  }
  else
  {
    auto created = [&]() -> Result<std::unique_ptr<MapDocument>> {
      if (!mapPath.empty())
      {
        return MapDocument::loadDocument(
          app.environmentConfig(),
          *gameInfo,
          format,
          MapDocument::DefaultWorldBounds,
          std::filesystem::path{mapPath},
          app.taskManager(),
          app.glManager().resourceManager());
      }
      return MapDocument::createDocument(
        app.environmentConfig(),
        *gameInfo,
        format,
        MapDocument::DefaultWorldBounds,
        app.taskManager(),
        app.glManager().resourceManager());
    }();

    if (created.is_error())
    {
      throw McpError{
        mcp::ErrorCode::OperationFailed,
        "could not create/open document: " + resultError(created)};
    }

    auto documentPtr = std::move(created).value();
    auto* newWindow = app.mapWindowManager().createMapWindow(std::move(documentPtr));
    if (newWindow == nullptr)
    {
      throw McpError{
        mcp::ErrorCode::OperationFailed, "MapWindowManager refused the document"};
    }
    m_window = newWindow;
  }

  if (!gamePath.empty())
  {
    map().setGamePath(std::filesystem::path{gamePath});
  }

  const auto fgdPath = mcp::stringFromJson(p.value(QLatin1String{"fgd"}));
  if (!fgdPath.empty())
  {
    mdl::setEntityDefinitionFile(
      map(), mdl::EntityDefinitionFileSpec::makeExternal(std::filesystem::path{fgdPath}));
    mdl::reloadEntityDefinitions(map());
  }

  const auto wads = mcp::stringArrayFromJson(p.value(QLatin1String{"wads"}));
  if (!wads.empty())
  {
    auto joined = std::string{};
    for (size_t i = 0; i < wads.size(); ++i)
    {
      if (i > 0)
      {
        joined += ';';
      }
      joined += wads[i];
    }
    auto tx = mdl::Transaction{map(), "MCP doc.new_or_open wads"};
    mdl::deselectAll(map());
    mdl::setEntityProperty(map(), mdl::EntityPropertyKeys::Wad, joined);
    tx.commit();
  }
  if (!gamePath.empty() || !wads.empty())
  {
    mdl::reloadMaterialCollections(map());
  }

  m_index.rebuild(document());
  armFileWatcher(map().path().string());

  auto result = QJsonObject{};
  result.insert(
    QLatin1String{"mapFormat"},
    QString::fromStdString(mapFormatName(map().worldNode().mapFormat())));
  result.insert(
    QLatin1String{"entityDefinitions"},
    static_cast<int>(map().entityDefinitionManager().definitions().size()));
  result.insert(
    QLatin1String{"materials"},
    static_cast<int>(map().materialManager().materials().size()));
  result.insert(QLatin1String{"worldBounds"}, mcp::bboxToJson(map().worldBounds()));
  result.insert(QLatin1String{"path"}, QString::fromStdString(map().path().string()));
  return result;
}

QJsonObject McpServerBridge::docState(const QJsonObject& p)
{
  (void)p;
  auto& m = map();
  m_index.rebuild(document());

  auto brushes = 0;
  auto entities = 0;
  auto groups = 0;
  const auto visit = [&](mdl::Node* node, auto&& self) -> void {
    if (dynamic_cast<mdl::BrushNode*>(node) != nullptr)
    {
      ++brushes;
    }
    if (dynamic_cast<mdl::EntityNode*>(node) != nullptr)
    {
      ++entities;
    }
    if (dynamic_cast<mdl::GroupNode*>(node) != nullptr)
    {
      ++groups;
    }
    for (auto* child : node->children())
    {
      self(child, self);
    }
  };
  for (auto* child : m.worldNode().children())
  {
    visit(child, visit);
  }

  auto counts = QJsonObject{};
  counts.insert(QLatin1String{"brushes"}, brushes);
  counts.insert(QLatin1String{"entities"}, entities);
  counts.insert(QLatin1String{"groups"}, groups);

  const auto* undoName = m.undoCommandName();

  auto result = QJsonObject{};
  result.insert(
    QLatin1String{"mapFormat"},
    QString::fromStdString(mapFormatName(m.worldNode().mapFormat())));
  result.insert(QLatin1String{"path"}, QString::fromStdString(m.path().string()));
  result.insert(QLatin1String{"counts"}, counts);
  result.insert(QLatin1String{"canUndo"}, undoName != nullptr);
  result.insert(QLatin1String{"undoName"}, undoName != nullptr ? QString::fromStdString(*undoName) : QString{});
  result.insert(
    QLatin1String{"selection"}, static_cast<int>(m.selection().nodes.size()));
  result.insert(QLatin1String{"worldBounds"}, mcp::bboxToJson(m.worldBounds()));
  result.insert(
    QLatin1String{"materials"},
    static_cast<int>(m.materialManager().materials().size()));
  return result;
}

QJsonObject McpServerBridge::docSave(const QJsonObject& p)
{
  auto& m = map();
  const auto path = mcp::stringFromJson(p.value(QLatin1String{"path"}));

  if (path.empty())
  {
    if (m.path().empty())
    {
      throw McpError{
        mcp::ErrorCode::InvalidParams, "document has no path; pass \"path\" to save"};
    }
    const auto saved = m.save();
    if (saved.is_error())
    {
      throw McpError{mcp::ErrorCode::OperationFailed, "save failed: " + resultError(saved)};
    }
  }
  else
  {
    const auto saved = m.saveAs(std::filesystem::path{path});
    if (saved.is_error())
    {
      throw McpError{mcp::ErrorCode::OperationFailed, "saveAs failed: " + resultError(saved)};
    }
  }

  noteOwnWrite(m.path().string());

  auto result = QJsonObject{};
  result.insert(QLatin1String{"path"}, QString::fromStdString(m.path().string()));
  result.insert(
    QLatin1String{"bytes"},
    static_cast<double>(QFileInfo{QString::fromStdString(m.path().string())}.size()));
  return result;
}

QJsonObject McpServerBridge::docUndo(const QJsonObject& p)
{
  (void)p;
  auto& m = map();
  const auto* name = m.undoCommandName();
  if (name == nullptr)
  {
    throw McpError{mcp::ErrorCode::BadState, "undo stack is empty"};
  }
  const auto commandName = *name;
  m.undoCommand();
  m_index.rebuild(document());

  return QJsonObject{{QLatin1String{"undone"}, QString::fromStdString(commandName)}};
}

QJsonObject McpServerBridge::docRedo(const QJsonObject& p)
{
  (void)p;
  auto& m = map();
  const auto* name = m.redoCommandName();
  if (name == nullptr)
  {
    throw McpError{mcp::ErrorCode::BadState, "redo stack is empty"};
  }
  const auto commandName = *name;
  m.redoCommand();
  m_index.rebuild(document());

  return QJsonObject{{QLatin1String{"redone"}, QString::fromStdString(commandName)}};
}

QJsonObject McpServerBridge::docClearSelection(const QJsonObject& p)
{
  (void)p;
  mdl::deselectAll(map());
  return QJsonObject{{QLatin1String{"selection"}, 0}};
}

QJsonObject McpServerBridge::docSelectById(const QJsonObject& p)
{
  m_index.rebuild(document());

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  if (ids.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "ids[] is required"};
  }

  auto missing = std::vector<std::string>{};
  const auto nodes = m_index.resolve(ids, missing);
  if (nodes.empty())
  {
    throw McpError{
      mcp::ErrorCode::OperationFailed,
      "none of the given ids exist (map changed since they were issued?)"};
  }

  if (!mcp::boolFromJson(p.value(QLatin1String{"additive"}), false))
  {
    mdl::deselectAll(map());
  }
  mdl::selectNodes(map(), nodes);

  auto result = QJsonObject{};
  result.insert(QLatin1String{"selected"}, static_cast<int>(nodes.size()));
  result.insert(QLatin1String{"missing"}, jsonFromStringVector(missing));
  return result;
}

QJsonObject McpServerBridge::docSelectByBBox(const QJsonObject& p)
{
  auto& m = map();
  const auto bounds = mcp::bboxFromJson(p.value(QLatin1String{"bounds"}));
  const auto mode = mcp::stringFromJson(p.value(QLatin1String{"mode"}), "intersects");

  auto selected = std::vector<mdl::Node*>{};
  const auto visit = [&](mdl::Node* node, auto&& self) -> void {
    if (dynamic_cast<mdl::BrushNode*>(node) != nullptr
        || dynamic_cast<mdl::EntityNode*>(node) != nullptr)
    {
      const auto& nodeBounds = node->logicalBounds();
      const auto hit = mode == "contains" ? bounds.contains(nodeBounds)
                                          : bounds.intersects(nodeBounds);
      if (hit)
      {
        selected.push_back(node);
      }
    }
    for (auto* child : node->children())
    {
      self(child, self);
    }
  };
  for (auto* child : m.worldNode().children())
  {
    visit(child, visit);
  }

  mdl::deselectAll(m);
  if (!selected.empty())
  {
    mdl::selectNodes(m, selected);
  }

  m_index.rebuild(document());
  auto ids = QJsonArray{};
  for (const auto* node : selected)
  {
    ids.append(QString::fromStdString(m_index.idOf(node)));
  }

  auto result = QJsonObject{};
  result.insert(QLatin1String{"selected"}, static_cast<int>(selected.size()));
  result.insert(QLatin1String{"ids"}, ids);
  return result;
}

QJsonObject McpServerBridge::docSelectAll(const QJsonObject& p)
{
  (void)p;
  auto& m = map();
  mdl::selectAllNodes(m);
  return QJsonObject{
    {QLatin1String{"selected"}, static_cast<int>(m.selection().nodes.size())}};
}

QJsonObject McpServerBridge::docSelection(const QJsonObject& p)
{
  (void)p;
  m_index.rebuild(document());

  auto array = QJsonArray{};
  for (auto* node : map().selection().nodes)
  {
    array.append(nodeSummary(node));
  }
  return QJsonObject{{QLatin1String{"nodes"}, array}};
}

QJsonObject McpServerBridge::docListNodeIds(const QJsonObject& p)
{
  (void)p;
  m_index.rebuild(document());

  auto array = QJsonArray{};
  const auto visit = [&](mdl::Node* node, auto&& self) -> void {
    array.append(nodeSummary(node));
    for (auto* child : node->children())
    {
      self(child, self);
    }
  };
  for (auto* child : map().worldNode().children())
  {
    visit(child, visit);
  }

  auto result = QJsonObject{};
  result.insert(QLatin1String{"nodes"}, array);
  result.insert(QLatin1String{"count"}, array.size());
  return result;
}

// ---------------------------------------------------------------------------
// geometry / CSG
// ---------------------------------------------------------------------------

QJsonObject McpServerBridge::geoCreateCuboid(const QJsonObject& p)
{
  auto& m = map();

  auto bounds = mcp::bboxFromJson(p.value(QLatin1String{"bounds"}));
  const auto grid = static_cast<int>(mcp::numberFromJson(p.value(QLatin1String{"grid"})));
  if (grid > 1)
  {
    bounds = vm::bbox3d{snapVector(bounds.min, grid), snapVector(bounds.max, grid)};
  }
  if (bounds.min.x() >= bounds.max.x() || bounds.min.y() >= bounds.max.y()
      || bounds.min.z() >= bounds.max.z())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "degenerate or inverted bounds"};
  }

  const auto material = mcp::stringFromJson(p.value(QLatin1String{"texture"}), "AAATRIGGER");
  const auto name = mcp::stringFromJson(p.value(QLatin1String{"name"}));

  auto builder = mdl::BrushBuilder{m.worldNode().mapFormat(), m.worldBounds()};
  auto built = builder.createCuboid(bounds, material);
  if (built.is_error())
  {
    throw McpError{mcp::ErrorCode::OperationFailed, "BrushBuilder: " + resultError(built)};
  }

  auto* node = new mdl::BrushNode{std::move(built.value())};

  auto transaction = mdl::Transaction{m, "MCP geo.create_cuboid"};
  const auto added = addToCurrentParent({node});
  if (added.empty())
  {
    transaction.cancel();
    throw McpError{
      mcp::ErrorCode::OperationFailed, "TrenchBroom refused to add the brush"};
  }
  transaction.commit();

  selectOnly(added);
  const auto ids = stampIds(added);

  auto result = QJsonObject{};
  result.insert(QLatin1String{"ids"}, jsonFromStringVector(ids));
  result.insert(QLatin1String{"bounds"}, mcp::bboxToJson(bounds));
  result.insert(QLatin1String{"material"}, QString::fromStdString(material));
  if (!name.empty())
  {
    result.insert(QLatin1String{"name"}, QString::fromStdString(name));
  }
  return result;
}


QJsonObject McpServerBridge::geoCreateBrushFromPoints(const QJsonObject& p)
{
  auto& m = map();

  auto points = std::vector<vm::vec3d>{};
  for (const auto& value : p.value(QLatin1String{"points"}).toArray())
  {
    points.push_back(mcp::vec3FromJson(value));
  }
  if (points.size() < 4)
  {
    throw McpError{
      mcp::ErrorCode::InvalidParams,
      "points[] needs at least 4 non-coplanar points (convex hull is built for you)"};
  }

  const auto material = mcp::stringFromJson(p.value(QLatin1String{"texture"}), "AAATRIGGER");
  m.setCurrentMaterialName(material);

  auto transaction = mdl::Transaction{m, "MCP geo.create_brush_from_points"};
  const auto ok = mdl::createBrush(m, points);
  transaction.finish(ok);
  if (!ok)
  {
    throw McpError{
      mcp::ErrorCode::OperationFailed,
      "could not build a convex brush from those points (degenerate or coplanar)"};
  }

  auto nodes = std::vector<mdl::Node*>{m.selection().nodes};
  auto result = QJsonObject{};
  result.insert(QLatin1String{"ids"}, jsonFromStringVector(stampIds(nodes)));
  result.insert(QLatin1String{"points"}, static_cast<int>(points.size()));
  return result;
}

QJsonObject McpServerBridge::geoCreateRoomHollow(const QJsonObject& p)
{
  auto& m = map();

  const auto bounds = mcp::bboxFromJson(p.value(QLatin1String{"bounds"}));
  const auto thickness =
    static_cast<int>(mcp::numberFromJson(p.value(QLatin1String{"wall_thickness"}), 16));
  // CSG Hollow derives the wall thickness from the grid, and Grid::setSize takes
  // the *exponent* (1 .. 256 units), not the thickness itself. Passing the raw
  // thickness trips the Grid contract and takes the whole process down.
  const auto exponent = thickness > 0 ? static_cast<int>(std::lround(std::log2(thickness))) : -99;
  if (!isPowerOfTwo(thickness) || thickness < 1 || thickness > 256
      || exponent < mdl::Grid::MinSize || exponent > mdl::Grid::MaxSize)
  {
    throw McpError{
      mcp::ErrorCode::InvalidParams,
      "wall_thickness must be a power of two between 1 and 256 units "
      "(CSG Hollow uses the grid, whose exponent range is 2^-3 .. 2^8)"};
  }

  const auto wallMaterial = mcp::stringFromJson(p.value(QLatin1String{"wall_tex"}));
  const auto floorMaterial = mcp::stringFromJson(p.value(QLatin1String{"floor_tex"}));
  const auto ceilingMaterial = mcp::stringFromJson(p.value(QLatin1String{"ceiling_tex"}));

  auto builder = mdl::BrushBuilder{m.worldNode().mapFormat(), m.worldBounds()};
  auto built = builder.createCuboid(
    bounds, wallMaterial.empty() ? std::string{"AAATRIGGER"} : wallMaterial);
  if (built.is_error())
  {
    throw McpError{mcp::ErrorCode::OperationFailed, "BrushBuilder: " + resultError(built)};
  }

  auto* outer = new mdl::BrushNode{std::move(built.value())};
  auto transaction = mdl::Transaction{m, "MCP geo.create_room_hollow"};

  const auto added = addToCurrentParent({outer});
  if (added.empty())
  {
    transaction.cancel();
    throw McpError{
      mcp::ErrorCode::OperationFailed, "TrenchBroom refused to add the brush"};
  }
  selectOnly(added);

  const auto previousGrid = m.grid().size();
  m.grid().setSize(exponent);
  const auto hollowed = mdl::csgHollow(m);
  m.grid().setSize(previousGrid);

  if (!hollowed)
  {
    transaction.cancel();
    throw McpError{
      mcp::ErrorCode::OperationFailed,
      "CSG Hollow failed; the room is too small for the requested wall thickness"};
  }

  auto fragments = std::vector<mdl::BrushNode*>{m.selection().brushes};
  auto fragmentNodes = std::vector<mdl::Node*>{};
  fragmentNodes.reserve(fragments.size());
  for (auto* brush : fragments)
  {
    fragmentNodes.push_back(brush);
  }

  auto texturesApplied = 0;
  const auto applyForFilter = [&](const FaceFilter filter, const std::string& material) {
    if (material.empty())
    {
      return;
    }
    auto handles = std::vector<mdl::BrushFaceHandle>{};
    for (auto* brush : fragments)
    {
      const auto& geometry = brush->brush();
      for (size_t i = 0; i < geometry.faceCount(); ++i)
      {
        if (faceMatches(geometry.face(i), filter))
        {
          handles.emplace_back(brush, i);
        }
      }
    }
    if (handles.empty())
    {
      return;
    }
    auto update = mdl::UpdateBrushFaceAttributes{};
    update.materialName = material;
    mdl::selectBrushFaces(m, handles);
    if (mdl::setBrushFaceAttributes(m, update))
    {
      texturesApplied += static_cast<int>(handles.size());
    }
  };

  applyForFilter(FaceFilter::Floor, floorMaterial);
  applyForFilter(FaceFilter::Ceiling, ceilingMaterial);
  selectOnly(fragmentNodes);

  transaction.commit();
  selectOnly(fragmentNodes);
  const auto ids = stampIds(fragmentNodes);

  auto result = QJsonObject{};
  result.insert(QLatin1String{"ids"}, jsonFromStringVector(ids));
  result.insert(QLatin1String{"fragments"}, static_cast<int>(fragmentNodes.size()));
  result.insert(QLatin1String{"wallThickness"}, thickness);
  result.insert(QLatin1String{"textureFacesChanged"}, texturesApplied);
  result.insert(QLatin1String{"bounds"}, mcp::bboxToJson(bounds));
  return result;
}

QJsonObject McpServerBridge::geoCsgSubtract(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  auto cutters = std::vector<mdl::Node*>{};
  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  std::unique_ptr<mdl::BrushNode> createdCutter;

  if (!ids.empty())
  {
    auto missing = std::vector<std::string>{};
    cutters = m_index.resolve(ids, missing);
    if (cutters.empty())
    {
      throw McpError{mcp::ErrorCode::OperationFailed, "no cutter brush matched the ids"};
    }
  }
  else
  {
    const auto bounds = mcp::bboxFromJson(p.value(QLatin1String{"cutter"}));
    const auto material =
      mcp::stringFromJson(p.value(QLatin1String{"texture"}), "AAATRIGGER");
    auto builder = mdl::BrushBuilder{m.worldNode().mapFormat(), m.worldBounds()};
    auto built = builder.createCuboid(bounds, material);
    if (built.is_error())
    {
      throw McpError{
        mcp::ErrorCode::OperationFailed, "BrushBuilder: " + resultError(built)};
    }
    createdCutter = std::make_unique<mdl::BrushNode>(std::move(built.value()));
    cutters.push_back(createdCutter.get());
  }

  auto transaction = mdl::Transaction{m, "MCP geo.csg_subtract"};
  if (createdCutter)
  {
    const auto added = addToCurrentParent({createdCutter.release()});
    if (added.empty())
    {
      transaction.cancel();
      throw McpError{mcp::ErrorCode::OperationFailed, "could not add the cutter brush"};
    }
    cutters = added;
  }

  selectOnly(cutters);
  if (!mdl::csgSubtract(m))
  {
    transaction.cancel();
    throw McpError{
      mcp::ErrorCode::OperationFailed,
      "CSG Subtract failed; the cutter must geometrically overlap the target brush"};
  }
  transaction.commit();

  auto nodes = std::vector<mdl::Node*>{};
  for (auto* brush : m.selection().brushes)
  {
    nodes.push_back(brush);
  }
  const auto resultIds = stampIds(nodes);

  auto result = QJsonObject{};
  result.insert(QLatin1String{"ids"}, jsonFromStringVector(resultIds));
  result.insert(QLatin1String{"fragments"}, static_cast<int>(nodes.size()));
  return result;
}

QJsonObject McpServerBridge::geoCsgUnion(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  if (ids.size() < 2)
  {
    throw McpError{
      mcp::ErrorCode::InvalidParams, "CSG Union needs at least two brush ids"};
  }

  auto missing = std::vector<std::string>{};
  selectOnly(m_index.resolve(ids, missing));

  auto transaction = mdl::Transaction{m, "MCP geo.csg_union"};
  if (!mdl::csgConvexMerge(m))
  {
    transaction.cancel();
    throw McpError{
      mcp::ErrorCode::OperationFailed,
      "CSG Convex Merge failed (brushes must be convex and mergeable)"};
  }
  transaction.commit();

  auto nodes = std::vector<mdl::Node*>{};
  for (auto* brush : m.selection().brushes)
  {
    nodes.push_back(brush);
  }
  const auto resultIds = stampIds(nodes);

  auto result = QJsonObject{};
  result.insert(QLatin1String{"ids"}, jsonFromStringVector(resultIds));
  result.insert(QLatin1String{"missing"}, jsonFromStringVector(missing));
  return result;
}

QJsonObject McpServerBridge::geoCsgIntersect(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  if (ids.size() < 2)
  {
    throw McpError{
      mcp::ErrorCode::InvalidParams, "CSG Intersect needs at least two brush ids"};
  }

  auto missing = std::vector<std::string>{};
  selectOnly(m_index.resolve(ids, missing));

  auto transaction = mdl::Transaction{m, "MCP geo.csg_intersect"};
  if (!mdl::csgIntersect(m))
  {
    transaction.cancel();
    throw McpError{mcp::ErrorCode::OperationFailed, "CSG Intersect failed"};
  }
  transaction.commit();

  auto nodes = std::vector<mdl::Node*>{};
  for (auto* brush : m.selection().brushes)
  {
    nodes.push_back(brush);
  }
  return QJsonObject{
    {QLatin1String{"ids"}, jsonFromStringVector(stampIds(nodes))}};
}

QJsonObject McpServerBridge::geoClipBrush(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  if (ids.empty())
  {
    throw McpError{
      mcp::ErrorCode::InvalidParams, "ids[] is required (brushes to clip)"};
  }

  const auto p1 = mcp::vec3FromJson(p.value(QLatin1String{"p1"}));
  const auto p2 = mcp::vec3FromJson(p.value(QLatin1String{"p2"}));
  const auto p3 = mcp::vec3FromJson(p.value(QLatin1String{"p3"}));
  if (vm::length(vm::cross(p2 - p1, p3 - p1)) < 1e-3)
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "clip points are collinear"};
  }

  auto missing = std::vector<std::string>{};
  const auto targets = m_index.resolve(ids, missing);
  selectOnly(targets);

  auto transaction = mdl::Transaction{m, "MCP geo.clip_brush"};

  auto faceResult = mdl::BrushFace::create(
    p1,
    p2,
    p3,
    mdl::BrushFaceAttributes{m.currentMaterialName()},
    m.worldNode().mapFormat());
  if (faceResult.is_error())
  {
    transaction.cancel();
    throw McpError{
      mcp::ErrorCode::OperationFailed, "could not build clip face: " + resultError(faceResult)};
  }
  const auto clipFace = faceResult.value();

  auto added = std::vector<mdl::Node*>{};
  for (auto* node : targets)
  {
    auto* brushNode = dynamic_cast<mdl::BrushNode*>(node);
    if (brushNode == nullptr)
    {
      continue;
    }
    auto clipped = brushNode->brush();
    if (clipped.clip(m.worldBounds(), clipFace).is_error())
    {
      continue;
    }
    added.push_back(new mdl::BrushNode{std::move(clipped)});
  }

  if (added.empty())
  {
    transaction.cancel();
    throw McpError{
      mcp::ErrorCode::OperationFailed,
      "clip failed; the plane must actually cut the selected brushes"};
  }

  const auto parent = mdl::parentForNodes(m, targets);
  mdl::removeNodes(m, targets);
  auto toAdd = std::map<mdl::Node*, std::vector<mdl::Node*>>{};
  toAdd[parent] = added;
  const auto resultNodes = mdl::addNodes(m, toAdd);
  selectOnly(resultNodes);
  if (!transaction.commit())
  {
    throw McpError{mcp::ErrorCode::OperationFailed, "clip transaction failed"};
  }

  auto result = QJsonObject{};
  result.insert(QLatin1String{"ids"}, jsonFromStringVector(stampIds(resultNodes)));
  return result;
}

QJsonObject McpServerBridge::geoTransform(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  if (!ids.empty())
  {
    auto missing = std::vector<std::string>{};
    selectOnly(m_index.resolve(ids, missing));
  }

  if (m.selection().nodes.empty())
  {
    throw McpError{
      mcp::ErrorCode::NoSelection, "nothing selected (pass ids[] or select first)"};
  }

  auto transaction = mdl::Transaction{m, "MCP geo.transform"};
  auto changed = false;

  if (p.contains(QLatin1String{"translate"}))
  {
    auto delta = mcp::vec3FromJson(p.value(QLatin1String{"translate"}));
    const auto grid =
      static_cast<int>(mcp::numberFromJson(p.value(QLatin1String{"grid"})));
    if (grid > 1)
    {
      delta = snapVector(delta, grid);
    }
    changed = mdl::translateSelection(m, delta) || changed;
  }

  if (p.contains(QLatin1String{"rotate"}))
  {
    const auto rotation = p.value(QLatin1String{"rotate"}).toObject();
    const auto center = mcp::vec3FromJson(rotation.value(QLatin1String{"center"}));
    const auto axis =
      mcp::vec3FromJson(rotation.value(QLatin1String{"axis"}), vm::vec3d{0, 0, 1});
    const auto degrees =
      mcp::numberFromJson(rotation.value(QLatin1String{"degrees"}), 0.0);
    changed =
      mdl::rotateSelection(m, center, vm::normalize(axis), degrees * Pi / 180.0) || changed;
  }

  if (p.contains(QLatin1String{"scale"}))
  {
    const auto scale = p.value(QLatin1String{"scale"}).toObject();
    const auto center = mcp::vec3FromJson(scale.value(QLatin1String{"center"}));
    const auto factors =
      mcp::vec3FromJson(scale.value(QLatin1String{"factors"}), vm::vec3d{1, 1, 1});
    changed = mdl::scaleSelection(m, center, factors) || changed;
  }

  if (!changed)
  {
    transaction.cancel();
    throw McpError{
      mcp::ErrorCode::InvalidParams,
      "no transform applied (provide translate, rotate or scale)"};
  }
  transaction.commit();

  auto nodes = std::vector<mdl::Node*>{m.selection().nodes};

  auto result = QJsonObject{};
  result.insert(QLatin1String{"ids"}, jsonFromStringVector(stampIds(nodes)));
  return result;
}

QJsonObject McpServerBridge::geoDeleteSelected(const QJsonObject& p)
{
  (void)p;
  auto& m = map();
  if (m.selection().nodes.empty())
  {
    throw McpError{mcp::ErrorCode::NoSelection, "nothing selected"};
  }

  const auto count = m.selection().nodes.size();
  auto transaction = mdl::Transaction{m, "MCP geo.delete_selected"};
  mdl::removeSelectedNodes(m);
  transaction.commit();

  return QJsonObject{{QLatin1String{"deleted"}, static_cast<int>(count)}};
}

QJsonObject McpServerBridge::geoDuplicateSelected(const QJsonObject& p)
{
  auto& m = map();
  if (m.selection().nodes.empty())
  {
    throw McpError{mcp::ErrorCode::NoSelection, "nothing selected"};
  }

  auto transaction = mdl::Transaction{m, "MCP geo.duplicate_selected"};
  mdl::duplicateSelectedNodes(m);
  if (p.contains(QLatin1String{"offset"}))
  {
    mdl::translateSelection(m, mcp::vec3FromJson(p.value(QLatin1String{"offset"})));
  }
  transaction.commit();

  auto nodes = std::vector<mdl::Node*>{m.selection().nodes};
  return QJsonObject{{QLatin1String{"ids"}, jsonFromStringVector(stampIds(nodes))}};
}

// ---------------------------------------------------------------------------
// materials (formerly textures)
// ---------------------------------------------------------------------------

QJsonObject McpServerBridge::matLoadCollection(const QJsonObject& p)
{
  auto& m = map();

  const auto wad = mcp::stringFromJson(p.value(QLatin1String{"path"}));
  if (wad.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "path is required"};
  }
  if (
    std::filesystem::path{wad}.is_absolute()
    && !QFileInfo::exists(QString::fromStdString(wad)))
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "no such file: " + wad};
  }

  auto currentWadStr = std::string{};
  if (const auto* prop = m.worldNode().entity().property(mdl::EntityPropertyKeys::Wad))
  {
    currentWadStr = *prop;
  }

  auto wadList = std::vector<std::string>{};
  auto alreadyLoaded = false;
  {
    auto start = size_t{0};
    while (start <= currentWadStr.size())
    {
      const auto sep = currentWadStr.find(';', start);
      const auto token = sep == std::string::npos ? currentWadStr.substr(start)
                                                  : currentWadStr.substr(start, sep - start);
      if (!token.empty())
      {
        if (token == wad)
        {
          alreadyLoaded = true;
        }
        wadList.push_back(token);
      }
      if (sep == std::string::npos)
      {
        break;
      }
      start = sep + 1;
    }
  }

  if (!alreadyLoaded)
  {
    wadList.push_back(wad);
    auto joined = std::string{};
    for (size_t i = 0; i < wadList.size(); ++i)
    {
      if (i > 0)
      {
        joined += ';';
      }
      joined += wadList[i];
    }
    auto tx = mdl::Transaction{m, "MCP tex.load_wad"};
    mdl::deselectAll(m);
    mdl::setEntityProperty(m, mdl::EntityPropertyKeys::Wad, joined);
    tx.commit();
    mdl::reloadMaterialCollections(m);
  }

  auto result = QJsonObject{};
  result.insert(QLatin1String{"wad"}, QString::fromStdString(wad));
  result.insert(QLatin1String{"alreadyLoaded"}, alreadyLoaded);
  result.insert(
    QLatin1String{"materials"},
    static_cast<int>(m.materialManager().materials().size()));
  return result;
}

QJsonObject McpServerBridge::matList(const QJsonObject& p)
{
  auto& m = map();
  const auto filter = mcp::stringFromJson(p.value(QLatin1String{"filter"}));
  const auto limit = static_cast<int>(mcp::numberFromJson(p.value(QLatin1String{"limit"})));

  auto array = QJsonArray{};
  auto matched = 0;
  for (const auto* material : m.materialManager().materials())
  {
    if (material == nullptr)
    {
      continue;
    }
    const auto& name = material->name();
    if (!filter.empty() && name.find(filter) == std::string::npos)
    {
      continue;
    }
    ++matched;
    if (limit > 0 && array.size() >= limit)
    {
      continue;
    }

    auto entry = QJsonObject{};
    entry.insert(QLatin1String{"name"}, QString::fromStdString(name));
    if (const auto* texture = material->texture())
    {
      entry.insert(QLatin1String{"width"}, static_cast<int>(texture->width()));
      entry.insert(QLatin1String{"height"}, static_cast<int>(texture->height()));
    }
    array.append(entry);
  }

  auto result = QJsonObject{};
  result.insert(QLatin1String{"textures"}, array);
  result.insert(QLatin1String{"matched"}, matched);
  result.insert(QLatin1String{"returned"}, array.size());
  return result;
}

QJsonObject McpServerBridge::matApply(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto material = mcp::stringFromJson(p.value(QLatin1String{"texture"}));
  if (material.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "texture is required"};
  }
  if (m.materialManager().material(material) == nullptr)
  {
    throw McpError{
      mcp::ErrorCode::InvalidParams,
      "material \"" + material + "\" is not loaded; call tex.load_wad first"};
  }

  const auto filter =
    parseFaceFilter(mcp::stringFromJson(p.value(QLatin1String{"faces"}), "all"));

  auto targets = std::vector<mdl::BrushNode*>{};
  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  if (ids.empty())
  {
    targets = std::vector<mdl::BrushNode*>{m.selection().brushes};
    if (targets.empty())
    {
      throw McpError{mcp::ErrorCode::NoSelection, "pass ids[] or select brushes first"};
    }
  }
  else
  {
    auto missing = std::vector<std::string>{};
    for (auto* node : m_index.resolve(ids, missing))
    {
      if (auto* brush = dynamic_cast<mdl::BrushNode*>(node))
      {
        targets.push_back(brush);
      }
    }
  }

  auto handles = std::vector<mdl::BrushFaceHandle>{};
  for (auto* brush : targets)
  {
    const auto& geometry = brush->brush();
    for (size_t i = 0; i < geometry.faceCount(); ++i)
    {
      if (faceMatches(geometry.face(i), filter))
      {
        handles.emplace_back(brush, i);
      }
    }
  }
  if (handles.empty())
  {
    throw McpError{
      mcp::ErrorCode::OperationFailed,
      "no faces matched the requested orientation (try faces=\"all\")"};
  }

  auto update = mdl::UpdateBrushFaceAttributes{};
  update.materialName = material;

  if (mcp::boolFromJson(p.value(QLatin1String{"reset_uv_to_world"}), false))
  {
    update.axis = mdl::AxisOp{mdl::ResetAxis{}};
    update.xOffset = mdl::ValueOp{mdl::SetValue{0.0f}};
    update.yOffset = mdl::ValueOp{mdl::SetValue{0.0f}};
    update.xScale = mdl::ValueOp{mdl::SetValue{1.0f}};
    update.yScale = mdl::ValueOp{mdl::SetValue{1.0f}};
    update.rotation = mdl::ValueOp{mdl::SetValue{0.0f}};
  }
  if (p.contains(QLatin1String{"offset"}))
  {
    const auto offset = p.value(QLatin1String{"offset"}).toArray();
    update.xOffset = mdl::ValueOp{
      mdl::SetValue{static_cast<float>(offset.at(0).toDouble(0.0))}};
    update.yOffset = mdl::ValueOp{
      mdl::SetValue{static_cast<float>(offset.at(1).toDouble(0.0))}};
  }
  if (p.contains(QLatin1String{"scale"}))
  {
    const auto scale = p.value(QLatin1String{"scale"}).toArray();
    update.xScale = mdl::ValueOp{
      mdl::SetValue{static_cast<float>(scale.at(0).toDouble(1.0))}};
    update.yScale = mdl::ValueOp{
      mdl::SetValue{static_cast<float>(scale.at(1).toDouble(1.0))}};
  }
  if (p.contains(QLatin1String{"rotation"}))
  {
    update.rotation = mdl::ValueOp{mdl::SetValue{
      static_cast<float>(mcp::numberFromJson(p.value(QLatin1String{"rotation"})))}};
  }

  auto previousNodes = std::vector<mdl::Node*>{m.selection().nodes};

  auto transaction = mdl::Transaction{m, "MCP tex.apply"};
  mdl::selectBrushFaces(m, handles);
  const auto applied = mdl::setBrushFaceAttributes(m, update);
  mdl::deselectAll(m);
  if (!previousNodes.empty())
  {
    mdl::selectNodes(m, previousNodes);
  }
  if (!applied)
  {
    transaction.cancel();
    throw McpError{mcp::ErrorCode::OperationFailed, "setBrushFaceAttributes failed"};
  }
  transaction.commit();

  auto result = QJsonObject{};
  result.insert(QLatin1String{"texture"}, QString::fromStdString(material));
  result.insert(QLatin1String{"faces"}, static_cast<int>(handles.size()));
  return result;
}

QJsonObject McpServerBridge::matSetProperties(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  auto targets = std::vector<mdl::BrushNode*>{};
  if (ids.empty())
  {
    targets = std::vector<mdl::BrushNode*>{m.selection().brushes};
  }
  else
  {
    auto missing = std::vector<std::string>{};
    for (auto* node : m_index.resolve(ids, missing))
    {
      if (auto* brush = dynamic_cast<mdl::BrushNode*>(node))
      {
        targets.push_back(brush);
      }
    }
  }
  if (targets.empty())
  {
    throw McpError{mcp::ErrorCode::NoSelection, "no brushes targeted"};
  }

  auto update = mdl::UpdateBrushFaceAttributes{};
  if (p.contains(QLatin1String{"surface_flags"}))
  {
    update.surfaceFlags = mdl::FlagOp{mdl::SetFlags{
      static_cast<int>(mcp::numberFromJson(p.value(QLatin1String{"surface_flags"})))}};
  }
  if (p.contains(QLatin1String{"content_flags"}))
  {
    update.surfaceContents = mdl::FlagOp{mdl::SetFlags{
      static_cast<int>(mcp::numberFromJson(p.value(QLatin1String{"content_flags"})))}};
  }

  auto handles = std::vector<mdl::BrushFaceHandle>{};
  for (auto* brush : targets)
  {
    for (size_t i = 0; i < brush->brush().faceCount(); ++i)
    {
      handles.emplace_back(brush, i);
    }
  }

  auto transaction = mdl::Transaction{m, "MCP tex.set_properties"};
  mdl::selectBrushFaces(m, handles);
  const auto applied = mdl::setBrushFaceAttributes(m, update);
  mdl::deselectAll(m);
  transaction.finish(applied);

  return QJsonObject{
    {QLatin1String{"faces"}, static_cast<int>(handles.size())},
    {QLatin1String{"applied"}, applied}};
}

// ---------------------------------------------------------------------------
// entities
// ---------------------------------------------------------------------------

QJsonObject McpServerBridge::entCreatePoint(const QJsonObject& p)
{
  auto& m = map();

  const auto classname = mcp::stringFromJson(p.value(QLatin1String{"classname"}));
  if (classname.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "classname is required"};
  }

  const auto* definition = m.entityDefinitionManager().definition(classname);
  if (definition == nullptr)
  {
    throw McpError{
      mcp::ErrorCode::InvalidParams,
      "unknown entity class \"" + classname
        + "\"; is the CS 1.6 .fgd loaded? (doc.new_or_open with \"fgd\")"};
  }
  if (mdl::getType(*definition) != mdl::EntityDefinitionType::Point)
  {
    throw McpError{
      mcp::ErrorCode::InvalidParams,
      "\"" + classname + "\" is a brush entity; use ent.create_brush_entity"};
  }

  const auto origin = mcp::vec3FromJson(p.value(QLatin1String{"origin"}));
  const auto angle = mcp::numberFromJson(p.value(QLatin1String{"angle"}));

  auto transaction = mdl::Transaction{m, "MCP ent.create_point_entity"};
  auto* node = mdl::createPointEntity(m, *definition, origin);
  if (node == nullptr)
  {
    transaction.cancel();
    throw McpError{
      mcp::ErrorCode::OperationFailed, "TrenchBroom refused to create the entity"};
  }

  if (angle != 0.0)
  {
    mdl::setEntityProperty(m, "angle", std::to_string(static_cast<int>(std::lround(angle))));
  }
  if (const auto properties = p.value(QLatin1String{"properties"}).toObject();
      !properties.isEmpty())
  {
    for (auto it = properties.begin(); it != properties.end(); ++it)
    {
      mdl::setEntityProperty(
        m,
        it.key().toStdString(),
        it.value().isString() ? it.value().toString().toStdString()
                              : QJsonDocument{QJsonArray{it.value()}}
                                  .toJson(QJsonDocument::Compact)
                                  .toStdString());
    }
  }

  transaction.commit();
  selectOnly({node});

  auto result = QJsonObject{};
  result.insert(QLatin1String{"id"}, QString::fromStdString(stampIds({node}).front()));
  result.insert(QLatin1String{"classname"}, QString::fromStdString(classname));
  result.insert(QLatin1String{"origin"}, mcp::vec3ToJson(origin));
  return result;
}

QJsonObject McpServerBridge::entCreateBrush(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto classname = mcp::stringFromJson(p.value(QLatin1String{"classname"}));
  if (classname.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "classname is required"};
  }

  const auto* definition = m.entityDefinitionManager().definition(classname);
  if (definition == nullptr || mdl::getType(*definition) != mdl::EntityDefinitionType::Brush)
  {
    throw McpError{
      mcp::ErrorCode::InvalidParams,
      "unknown brush entity class \"" + classname
        + "\"; is the CS 1.6 .fgd loaded? (doc.new_or_open with \"fgd\")"};
  }

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  if (!ids.empty())
  {
    auto missing = std::vector<std::string>{};
    selectOnly(m_index.resolve(ids, missing));
  }

  if (m.selection().brushes.empty())
  {
    throw McpError{
      mcp::ErrorCode::NoSelection,
      "select world brushes first (ids[]) before converting them to a brush entity"};
  }

  auto transaction = mdl::Transaction{m, "MCP ent.create_brush_entity"};
  auto* node = mdl::createBrushEntity(m, *definition);
  if (node == nullptr)
  {
    transaction.cancel();
    throw McpError{
      mcp::ErrorCode::OperationFailed, "TrenchBroom refused to create the brush entity"};
  }

  if (const auto properties = p.value(QLatin1String{"properties"}).toObject();
      !properties.isEmpty())
  {
    for (auto it = properties.begin(); it != properties.end(); ++it)
    {
      mdl::setEntityProperty(
        m,
        it.key().toStdString(),
        it.value().isString() ? it.value().toString().toStdString()
                              : QJsonDocument{QJsonArray{it.value()}}
                                  .toJson(QJsonDocument::Compact)
                                  .toStdString());
    }
  }

  transaction.commit();

  auto result = QJsonObject{};
  result.insert(QLatin1String{"id"}, QString::fromStdString(stampIds({node}).front()));
  result.insert(QLatin1String{"classname"}, QString::fromStdString(classname));
  return result;
}

QJsonObject McpServerBridge::entSetProperty(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto key = mcp::stringFromJson(p.value(QLatin1String{"key"}));
  const auto value = mcp::stringFromJson(p.value(QLatin1String{"value"}));
  if (key.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "key is required"};
  }

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  if (!ids.empty())
  {
    auto missing = std::vector<std::string>{};
    selectOnly(m_index.resolve(ids, missing));
  }

  if (m.selection().entities.empty())
  {
    throw McpError{mcp::ErrorCode::NoSelection, "select entity nodes first (ids[])"};
  }

  auto transaction = mdl::Transaction{m, "MCP ent.set_property"};
  const auto applied = mdl::setEntityProperty(m, key, value);
  transaction.finish(applied);

  return QJsonObject{
    {QLatin1String{"key"}, QString::fromStdString(key)},
    {QLatin1String{"value"}, QString::fromStdString(value)},
    {QLatin1String{"entities"}, static_cast<int>(m.selection().entities.size())},
    {QLatin1String{"applied"}, applied}};
}

QJsonObject McpServerBridge::entList(const QJsonObject& p)
{
  m_index.rebuild(document());
  const auto classname = mcp::stringFromJson(p.value(QLatin1String{"classname"}));

  auto array = QJsonArray{};
  const auto visit = [&](mdl::Node* node, auto&& self) -> void {
    if (auto* entityNode = dynamic_cast<mdl::EntityNode*>(node))
    {
      const auto* name = entityNode->entity().property(mdl::EntityPropertyKeys::Classname);
      if (classname.empty() || (name != nullptr && *name == classname))
      {
        array.append(nodeSummary(entityNode));
      }
    }
    for (auto* child : node->children())
    {
      self(child, self);
    }
  };
  for (auto* child : map().worldNode().children())
  {
    visit(child, visit);
  }

  return QJsonObject{
    {QLatin1String{"entities"}, array}, {QLatin1String{"count"}, array.size()}};
}

QJsonObject McpServerBridge::prefabPaste(const QJsonObject& p)
{
  auto& m = map();

  const auto snippet = mcp::stringFromJson(p.value(QLatin1String{"map"}));
  if (snippet.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "map (a .map snippet) is required"};
  }
  const auto origin = mcp::vec3FromJson(p.value(QLatin1String{"origin"}));
  const auto yaw = mcp::numberFromJson(p.value(QLatin1String{"rotation"}));

  auto transaction = mdl::Transaction{m, "MCP prefab.paste"};
  mdl::deselectAll(m);
  mdl::paste(m, snippet);
  if (m.selection().nodes.empty())
  {
    transaction.cancel();
    throw McpError{
      mcp::ErrorCode::OperationFailed,
      "paste produced no nodes; the snippet must use the document's map format"};
  }

  if (yaw != 0.0)
  {
    const auto& bounds = m.selectionBounds();
    if (bounds)
    {
      mdl::rotateSelection(m, bounds->center(), vm::vec3d{0, 0, 1}, yaw * Pi / 180.0);
    }
  }
  if (origin != vm::vec3d{0, 0, 0})
  {
    const auto& bounds = m.selectionBounds();
    if (bounds)
    {
      mdl::translateSelection(m, origin - bounds->min);
    }
  }
  transaction.commit();

  auto nodes = std::vector<mdl::Node*>{m.selection().nodes};
  return QJsonObject{{QLatin1String{"ids"}, jsonFromStringVector(stampIds(nodes))}};
}

// ---------------------------------------------------------------------------
// view / capture
// ---------------------------------------------------------------------------

QJsonObject McpServerBridge::viewCapture3D(const QJsonObject& p)
{
  auto& window = requireWindow();
  auto* view = window.findChild<MapView3D*>();
  if (view == nullptr)
  {
    if (auto* container = window.findChild<SwitchableMapViewContainer*>())
    {
      container->switchToMapView(MapViewLayout::FourPanes);
      view = window.findChild<MapView3D*>();
    }
  }
  if (view == nullptr)
  {
    throw McpError{
      mcp::ErrorCode::BadState, "no 3D viewport exists; call view.set_layout first"};
  }

  auto& camera = static_cast<MapViewBase*>(view)->camera();

  const auto position = mcp::vec3FromJson(p.value(QLatin1String{"pos"}));
  const auto lookAt =
    mcp::vec3FromJson(p.value(QLatin1String{"look_at"}), position - vm::vec3d{0, 0, 1024});
  const auto hasYawPitch = p.contains(QLatin1String{"yaw"})
                           || p.contains(QLatin1String{"pitch"});

  camera.moveTo(vm::vec3f{
    static_cast<float>(position.x()),
    static_cast<float>(position.y()),
    static_cast<float>(position.z())});

  if (hasYawPitch)
  {
    const auto yaw =
      static_cast<float>(mcp::numberFromJson(p.value(QLatin1String{"yaw"}))) * static_cast<float>(Pi) / 180.0f;
    const auto pitch =
      static_cast<float>(mcp::numberFromJson(p.value(QLatin1String{"pitch"}))) * static_cast<float>(Pi) / 180.0f;
    const auto direction = vm::vec3f{
      std::cos(pitch) * std::cos(yaw), std::cos(pitch) * std::sin(yaw), std::sin(pitch)};
    camera.setDirection(direction, vm::vec3f{0, 0, 1});
  }
  else
  {
    camera.lookAt(
      vm::vec3f{
        static_cast<float>(lookAt.x()),
        static_cast<float>(lookAt.y()),
        static_cast<float>(lookAt.z())},
      vm::vec3f{0, 0, 1});
  }

  view->update();
  view->repaint();

  const auto image = view->grabFramebuffer();
  if (image.isNull())
  {
    throw McpError{
      mcp::ErrorCode::OperationFailed, "framebuffer grab returned a null image"};
  }

  auto annotated = image;
  if (mcp::boolFromJson(p.value(QLatin1String{"overlay"}), true))
  {
    auto painter = QPainter{&annotated};
    painter.setRenderHint(QPainter::Antialiasing, true);
    painter.setPen(QPen{QColor{90, 230, 120}});
    const auto cx = annotated.width() / 2;
    const auto cy = annotated.height() / 2;
    painter.drawLine(cx - 12, cy, cx + 12, cy);
    painter.drawLine(cx, cy - 12, cx, cy + 12);
    painter.setPen(QPen{QColor{255, 255, 255, 200}});
    painter.drawText(
      8,
      18,
      QString::fromStdString(
        "pos (" + std::to_string(static_cast<int>(position.x())) + ", "
        + std::to_string(static_cast<int>(position.y())) + ", "
        + std::to_string(static_cast<int>(position.z())) + ")"));
    painter.end();
  }

  auto* outPath = std::getenv("TB_MCP_CAPTURE_DIR");
  const auto defaultDir =
    outPath != nullptr ? std::string{outPath} : QDir::tempPath().toStdString();
  const auto fileName = mcp::stringFromJson(
    p.value(QLatin1String{"path"}),
    defaultDir + "/tb_mcp_3d_"
      + std::to_string(
        std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::system_clock::now().time_since_epoch())
          .count())
      + ".png");

  QDir{}.mkpath(QFileInfo{QString::fromStdString(fileName)}.absolutePath());
  if (!annotated.save(QString::fromStdString(fileName), "PNG"))
  {
    throw McpError{mcp::ErrorCode::OperationFailed, "could not write " + fileName};
  }

  auto result = QJsonObject{};
  result.insert(QLatin1String{"path"}, QString::fromStdString(fileName));
  result.insert(QLatin1String{"width"}, annotated.width());
  result.insert(QLatin1String{"height"}, annotated.height());
  if (mcp::boolFromJson(p.value(QLatin1String{"base64"}), false))
  {
    auto buffer = QByteArray{};
    auto device = QBuffer{&buffer};
    device.open(QIODevice::WriteOnly);
    annotated.save(&device, "PNG");
    result.insert(QLatin1String{"pngBase64"}, QString::fromLatin1(buffer.toBase64()));
  }
  return result;
}

QJsonObject McpServerBridge::viewCaptureOrtho(const QJsonObject& p)
{
  auto& window = requireWindow();
  auto* view = window.findChild<MapView2D*>();
  if (view == nullptr)
  {
    if (auto* container = window.findChild<SwitchableMapViewContainer*>())
    {
      container->switchToMapView(MapViewLayout::FourPanes);
      view = window.findChild<MapView2D*>();
    }
  }
  if (view == nullptr)
  {
    throw McpError{
      mcp::ErrorCode::BadState, "no 2D viewport exists; call view.set_layout first"};
  }

  auto& m = map();
  const auto axis = mcp::stringFromJson(p.value(QLatin1String{"axis"}), "top");

  auto bounds = [&]() {
    if (p.contains(QLatin1String{"bounds"}))
    {
      return mcp::bboxFromJson(p.value(QLatin1String{"bounds"}));
    }
    const auto& selectionBounds = m.selectionBounds();
    return selectionBounds.value_or(mcp::bboxFromJson(QJsonValue{}));
  }();

  auto center = bounds.center();
  if (p.contains(QLatin1String{"center"}))
  {
    center = mcp::vec3FromJson(p.value(QLatin1String{"center"}));
  }

  auto direction = vm::vec3f{0, 0, -1};
  auto up = vm::vec3f{0, 1, 0};
  auto worldWidth = bounds.size().x();
  auto worldHeight = bounds.size().y();

  if (axis == "front")
  {
    direction = vm::vec3f{0, 1, 0};
    up = vm::vec3f{0, 0, 1};
    worldWidth = bounds.size().x();
    worldHeight = bounds.size().z();
  }
  else if (axis == "side")
  {
    direction = vm::vec3f{1, 0, 0};
    up = vm::vec3f{0, 0, 1};
    worldWidth = bounds.size().y();
    worldHeight = bounds.size().z();
  }

  const auto padding =
    mcp::numberFromJson(p.value(QLatin1String{"padding"}), 64.0);
  worldWidth += padding * 2;
  worldHeight += padding * 2;

  auto& camera = static_cast<MapViewBase*>(view)->camera();
  camera.moveTo(vm::vec3f{
    static_cast<float>(center.x()),
    static_cast<float>(center.y()),
    static_cast<float>(center.z())});
  camera.setDirection(direction, up);

  const auto& viewport = camera.viewport();
  if (viewport.width > 0 && viewport.height > 0 && worldWidth > 0 && worldHeight > 0)
  {
    const auto zoomX = static_cast<float>(viewport.width) / static_cast<float>(worldWidth);
    const auto zoomY = static_cast<float>(viewport.height) / static_cast<float>(worldHeight);
    camera.setZoom(std::min(zoomX, zoomY));
  }

  view->update();
  view->repaint();

  const auto image = view->grabFramebuffer();
  if (image.isNull())
  {
    throw McpError{
      mcp::ErrorCode::OperationFailed, "framebuffer grab returned a null image"};
  }

  auto annotated = image;
  if (mcp::boolFromJson(p.value(QLatin1String{"overlay"}), true))
  {
    auto painter = QPainter{&annotated};
    painter.setRenderHint(QPainter::Antialiasing, true);

    const auto projection = camera.projectionMatrix();
    const auto viewMatrix = camera.viewMatrix();
    const auto toScreen = [&](const vm::vec3d& world) {
      const auto clip = projection * viewMatrix
                        * vm::vec4f{
                          static_cast<float>(world.x()),
                          static_cast<float>(world.y()),
                          static_cast<float>(world.z()),
                          1.0f};
      const auto w = clip.w() != 0.0f ? clip.w() : 1.0f;
      return QPointF{
        (static_cast<double>(clip.x() / w) * 0.5 + 0.5) * annotated.width(),
        (1.0 - (static_cast<double>(clip.y() / w) * 0.5 + 0.5)) * annotated.height()};
    };

    const auto gridStep = 256.0;
    for (double x = std::floor(bounds.min.x() / gridStep) * gridStep;
         x <= bounds.max.x();
         x += gridStep)
    {
      painter.setPen(QPen{QColor{40, 40, 40, 180}, 1.0});
      painter.drawLine(
        toScreen({x, bounds.min.y(), center.z()}),
        toScreen({x, bounds.max.y(), center.z()}));
      painter.setPen(QPen{QColor{255, 255, 255, 160}});
      painter.drawText(
        toScreen({x, bounds.min.y(), center.z()}) + QPointF{2, 12},
        QString::number(static_cast<int>(x)));
    }
    for (double y = std::floor(bounds.min.y() / gridStep) * gridStep;
         y <= bounds.max.y();
         y += gridStep)
    {
      painter.setPen(QPen{QColor{40, 40, 40, 180}, 1.0});
      painter.drawLine(
        toScreen({bounds.min.x(), y, center.z()}),
        toScreen({bounds.max.x(), y, center.z()}));
    }

    painter.setPen(QPen{QColor{255, 80, 80, 220}, 2.0});
    painter.drawRect(QRectF{
      toScreen({bounds.min.x(), bounds.max.y(), center.z()}),
      toScreen({bounds.max.x(), bounds.min.y(), center.z()})});
    painter.end();
  }

  auto* outDirEnv = std::getenv("TB_MCP_CAPTURE_DIR");
  const auto defaultDir =
    outDirEnv != nullptr ? std::string{outDirEnv} : QDir::tempPath().toStdString();
  const auto fileName = mcp::stringFromJson(
    p.value(QLatin1String{"path"}),
    defaultDir + "/tb_mcp_ortho_" + axis + "_"
      + std::to_string(
        std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::system_clock::now().time_since_epoch())
          .count())
      + ".png");

  QDir{}.mkpath(QFileInfo{QString::fromStdString(fileName)}.absolutePath());
  if (!annotated.save(QString::fromStdString(fileName), "PNG"))
  {
    throw McpError{mcp::ErrorCode::OperationFailed, "could not write " + fileName};
  }

  auto result = QJsonObject{};
  result.insert(QLatin1String{"path"}, QString::fromStdString(fileName));
  result.insert(QLatin1String{"width"}, annotated.width());
  result.insert(QLatin1String{"height"}, annotated.height());
  result.insert(QLatin1String{"axis"}, QString::fromStdString(axis));
  result.insert(QLatin1String{"bounds"}, mcp::bboxToJson(bounds));
  return result;
}

QJsonObject McpServerBridge::viewLoadPointFile(const QJsonObject& p)
{
  auto& doc = document();
  const auto path = mcp::stringFromJson(p.value(QLatin1String{"path"}));
  if (path.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "path is required"};
  }
  if (!QFileInfo::exists(QString::fromStdString(path)))
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "no such point file: " + path};
  }

  doc.loadPointFile(std::filesystem::path{path});
  if (!doc.isPointFileLoaded())
  {
    throw McpError{
      mcp::ErrorCode::OperationFailed,
      "TrenchBroom could not parse the .pts file (expected \"x y z\" triples)"};
  }

  if (auto* view = requireWindow().findChild<MapView3D*>())
  {
    view->update();
  }

  return QJsonObject{
    {QLatin1String{"loaded"}, true}, {QLatin1String{"path"}, QString::fromStdString(path)}};
}

QJsonObject McpServerBridge::viewSetLayout(const QJsonObject& p)
{
  auto& window = requireWindow();
  auto* container = window.findChild<SwitchableMapViewContainer*>();
  if (container == nullptr)
  {
    throw McpError{mcp::ErrorCode::BadState, "no map view container found"};
  }

  const auto layout = mcp::stringFromJson(p.value(QLatin1String{"layout"}), "four");
  if (layout == "one")
  {
    container->switchToMapView(MapViewLayout::OnePane);
  }
  else if (layout == "two")
  {
    container->switchToMapView(MapViewLayout::TwoPanes);
  }
  else if (layout == "three")
  {
    container->switchToMapView(MapViewLayout::ThreePanes);
  }
  else
  {
    container->switchToMapView(MapViewLayout::FourPanes);
  }

  return QJsonObject{{QLatin1String{"layout"}, QString::fromStdString(layout)}};
}

QJsonObject McpServerBridge::viewCameraInfo(const QJsonObject& p)
{
  (void)p;
  auto* view = requireWindow().findChild<MapView3D*>();
  if (view == nullptr)
  {
    throw McpError{mcp::ErrorCode::BadState, "no 3D viewport"};
  }

  const auto& camera = static_cast<MapViewBase*>(view)->camera();
  const auto& position = camera.position();
  const auto& direction = camera.direction();

  auto result = QJsonObject{};
  result.insert(
    QLatin1String{"position"},
    mcp::vec3ToJson(
      vm::vec3d{position.x(), position.y(), position.z()}));
  result.insert(
    QLatin1String{"direction"},
    mcp::vec3ToJson(
      vm::vec3d{direction.x(), direction.y(), direction.z()}));
  result.insert(QLatin1String{"viewportHeight"}, camera.viewport().height);
  return result;
}

// ---------------------------------------------------------------------------
// validation
// ---------------------------------------------------------------------------

QJsonObject McpServerBridge::valGetIssues(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto includeHidden =
    mcp::boolFromJson(p.value(QLatin1String{"include_hidden"}), false);
  const auto validators = m.worldNode().registeredValidators();

  auto issues = QJsonArray{};
  const auto collect = [&](mdl::Node* node, auto&& self) -> void {
    for (const auto* issue : node->issues(validators))
    {
      if (issue == nullptr || (!includeHidden && issue->hidden()))
      {
        continue;
      }
      auto entry = QJsonObject{};
      entry.insert(QLatin1String{"id"}, QString::fromStdString(m_index.idOf(node)));
      entry.insert(QLatin1String{"type"}, issue->type());
      entry.insert(
        QLatin1String{"description"}, QString::fromStdString(issue->description()));
      entry.insert(
        QLatin1String{"line"}, static_cast<double>(issue->lineNumber()));
      entry.insert(QLatin1String{"hidden"}, issue->hidden());
      issues.append(entry);
    }
    for (auto* child : node->children())
    {
      self(child, self);
    }
  };
  for (auto* child : m.worldNode().children())
  {
    collect(child, collect);
  }

  auto result = QJsonObject{};
  result.insert(QLatin1String{"issues"}, issues);
  result.insert(QLatin1String{"count"}, issues.size());
  return result;
}

// ---------------------------------------------------------------------------
// organisation
// ---------------------------------------------------------------------------

QJsonObject McpServerBridge::layerCreate(const QJsonObject& p)
{
  auto& m = map();

  const auto name = mcp::stringFromJson(p.value(QLatin1String{"name"}));
  if (name.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "name is required"};
  }
  if (name == "Default")
  {
    throw McpError{mcp::ErrorCode::BadState, "the \"Default\" layer always exists"};
  }
  for (auto* existing : m.worldNode().allLayers())
  {
    if (existing->layer().name() == name)
    {
      throw McpError{
        mcp::ErrorCode::BadState, "a layer named \"" + name + "\" already exists"};
    }
  }

  auto layer = mdl::Layer{name};
  const auto customLayers = m.worldNode().customLayersUserSorted();
  layer.setSortIndex(customLayers.empty() ? 0 : customLayers.back()->layer().sortIndex() + 1);

  auto* node = new mdl::LayerNode{std::move(layer)};

  auto transaction = mdl::Transaction{m, "MCP layer.create " + name};
  auto toAdd = std::map<mdl::Node*, std::vector<mdl::Node*>>{};
  toAdd[&m.worldNode()] = {node};
  if (mdl::addNodes(m, toAdd).empty())
  {
    transaction.cancel();
    throw McpError{
      mcp::ErrorCode::OperationFailed, "TrenchBroom refused to create the layer"};
  }
  transaction.commit();

  return QJsonObject{
    {QLatin1String{"name"}, QString::fromStdString(name)},
    {QLatin1String{"ids"}, jsonFromStringVector(stampIds({node}))}};
}

QJsonObject McpServerBridge::layerSetActive(const QJsonObject& p)
{
  auto& m = map();

  const auto name = mcp::stringFromJson(p.value(QLatin1String{"name"}));
  if (name.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "name is required"};
  }

  for (auto* layer : m.worldNode().allLayers())
  {
    if (layer->layer().name() == name)
    {
      mdl::setCurrentLayer(m, layer);
      return QJsonObject{{QLatin1String{"active"}, QString::fromStdString(name)}};
    }
  }
  throw McpError{mcp::ErrorCode::InvalidParams, "no such layer: " + name};
}

QJsonObject McpServerBridge::layerList(const QJsonObject& p)
{
  (void)p;
  auto& m = map();

  auto active = std::string{};
  if (auto* current = m.editorContext().currentLayer())
  {
    active = current->layer().name();
  }

  auto array = QJsonArray{};
  for (const auto* layer : m.worldNode().allLayersUserSorted())
  {
    auto entry = QJsonObject{};
    entry.insert(QLatin1String{"name"}, QString::fromStdString(layer->layer().name()));
    entry.insert(QLatin1String{"active"}, layer->layer().name() == active);
    entry.insert(QLatin1String{"sortIndex"}, layer->layer().sortIndex());
    entry.insert(QLatin1String{"default"}, layer->layer().defaultLayer());
    entry.insert(QLatin1String{"nodes"}, static_cast<int>(layer->children().size()));
    array.append(entry);
  }

  auto result = QJsonObject{};
  result.insert(QLatin1String{"layers"}, array);
  result.insert(QLatin1String{"active"}, QString::fromStdString(active));
  return result;
}

QJsonObject McpServerBridge::groupCreate(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto name = mcp::stringFromJson(p.value(QLatin1String{"name"}), "group");
  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  if (ids.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "ids[] is required"};
  }

  auto missing = std::vector<std::string>{};
  const auto nodes = m_index.resolve(ids, missing);
  if (nodes.empty())
  {
    throw McpError{mcp::ErrorCode::OperationFailed, "no node matched the ids"};
  }

  selectOnly(nodes);

  auto transaction = mdl::Transaction{m, "MCP group.create " + name};
  auto* group = mdl::groupSelectedNodes(m, name);
  if (group == nullptr)
  {
    transaction.cancel();
    throw McpError{
      mcp::ErrorCode::OperationFailed,
      "grouping failed; nodes must share the same layer and parent"};
  }
  transaction.commit();

  auto result = QJsonObject{};
  result.insert(QLatin1String{"name"}, QString::fromStdString(group->group().name()));
  result.insert(QLatin1String{"ids"}, jsonFromStringVector(stampIds({group})));
  result.insert(QLatin1String{"children"}, static_cast<int>(group->children().size()));
  return result;
}

QJsonObject McpServerBridge::groupUngroup(const QJsonObject& p)
{
  (void)p;
  auto& m = map();
  if (m.selection().groups.empty())
  {
    throw McpError{mcp::ErrorCode::NoSelection, "select a group first"};
  }

  auto transaction = mdl::Transaction{m, "MCP group.ungroup"};
  mdl::ungroupSelectedNodes(m);
  transaction.commit();

  auto nodes = std::vector<mdl::Node*>{m.selection().nodes};
  return QJsonObject{{QLatin1String{"ids"}, jsonFromStringVector(stampIds(nodes))}};
}

// ---------------------------------------------------------------------------
// snapshots and disk auto-reload
// ---------------------------------------------------------------------------

std::string McpServerBridge::snapshotDir() const
{
  if (const auto* env = std::getenv("TB_MCP_SNAPSHOT_DIR"))
  {
    return env;
  }
  const auto& path = map().path();
  if (!path.empty())
  {
    return (path.parent_path() / ".map_history").string();
  }
  return (std::filesystem::temp_directory_path() / "tb_map_history").string();
}

QJsonObject McpServerBridge::snapshotSave(const QJsonObject& p)
{
  auto& m = map();

  auto label = mcp::stringFromJson(p.value(QLatin1String{"label"}), "snap");
  for (auto& ch : label)
  {
    if (!std::isalnum(static_cast<unsigned char>(ch)) && ch != '-' && ch != '_')
    {
      ch = '_';
    }
  }

  const auto dir = snapshotDir();
  QDir{}.mkpath(QString::fromStdString(dir));

  const auto stamp =
    QDateTime::currentDateTimeUtc().toString(QStringLiteral("yyyyMMdd-HHmmss-zzz"));
  const auto fileName = QString::fromStdString(dir) + "/" + stamp + "_"
                        + QString::fromStdString(label) + ".map";

  const auto written = m.saveTo(std::filesystem::path{fileName.toStdString()});
  if (written.is_error())
  {
    throw McpError{
      mcp::ErrorCode::OperationFailed, "snapshot failed: " + resultError(written)};
  }

  auto result = QJsonObject{};
  result.insert(QLatin1String{"id"}, QFileInfo{fileName}.fileName());
  result.insert(QLatin1String{"path"}, fileName);
  result.insert(QLatin1String{"bytes"}, static_cast<double>(QFileInfo{fileName}.size()));
  return result;
}

QJsonObject McpServerBridge::snapshotList(const QJsonObject& p)
{
  (void)p;
  const auto dir = QString::fromStdString(snapshotDir());
  const auto files = QDir{dir}.entryInfoList(
    QStringList{QStringLiteral("*.map")}, QDir::Files, QDir::Time);

  auto array = QJsonArray{};
  for (const auto& info : files)
  {
    auto entry = QJsonObject{};
    entry.insert(QLatin1String{"id"}, info.fileName());
    entry.insert(QLatin1String{"path"}, info.absoluteFilePath());
    entry.insert(QLatin1String{"bytes"}, static_cast<double>(info.size()));
    entry.insert(QLatin1String{"modified"}, info.lastModified().toString(Qt::ISODate));
    array.append(entry);
  }

  return QJsonObject{
    {QLatin1String{"dir"}, dir},
    {QLatin1String{"snapshots"}, array},
    {QLatin1String{"count"}, static_cast<int>(files.size())}};
}

QJsonObject McpServerBridge::snapshotRestore(const QJsonObject& p)
{
  auto& doc = document();

  const auto id = mcp::stringFromJson(p.value(QLatin1String{"id"}));
  if (id.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "id is required (see snapshot.list)"};
  }
  if (id.find('/') != std::string::npos || id.find("..") != std::string::npos)
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "id must be a bare file name"};
  }

  const auto path =
    QString::fromStdString(snapshotDir()) + "/" + QString::fromStdString(id);
  if (!QFileInfo::exists(path))
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "no such snapshot: " + id};
  }

  const auto restored = doc.load(
    map().environmentConfig(),
    map().gameInfo(),
    map().worldNode().mapFormat(),
    map().worldBounds(),
    std::filesystem::path{path.toStdString()});
  if (restored.is_error())
  {
    throw McpError{
      mcp::ErrorCode::OperationFailed, "restore failed: " + resultError(restored)};
  }

  m_index.rebuild(doc);
  armFileWatcher(map().path().string());

  auto result = QJsonObject{};
  result.insert(QLatin1String{"restored"}, QString::fromStdString(id));
  result.insert(QLatin1String{"path"}, QString::fromStdString(map().path().string()));
  return result;
}

void McpServerBridge::armFileWatcher(const std::string& path)
{
  if (!m_watchedPath.isEmpty())
  {
    m_watcher.removePath(m_watchedPath);
    m_watchedPath.clear();
  }

  if (path.empty())
  {
    return;
  }

  const auto filePath = QString::fromStdString(path);
  if (!QFileInfo::exists(filePath))
  {
    return;
  }

  m_watchedPath = filePath;
  m_lastKnownMtime = QFileInfo{filePath}.lastModified();
  m_watcher.addPath(filePath);
}

void McpServerBridge::noteOwnWrite(const std::string& path)
{
  if (path.empty())
  {
    return;
  }
  const auto filePath = QString::fromStdString(path);
  if (QFileInfo::exists(filePath))
  {
    m_lastKnownMtime = QFileInfo{filePath}.lastModified();
  }
  armFileWatcher(path);
}

void McpServerBridge::onWatchedFileChanged(const QString& path)
{
  if (m_suppressReload)
  {
    return;
  }

  QTimer::singleShot(300, this, [this, path]() {
    if (m_suppressReload)
    {
      return;
    }

    const auto info = QFileInfo{path};
    if (!info.exists() || info.lastModified() == m_lastKnownMtime)
    {
      armFileWatcher(path.toStdString());
      return;
    }

    m_suppressReload = true;
    auto& doc = document();
    const auto reloaded = doc.reload();
    m_suppressReload = false;

    if (reloaded.is_success())
    {
      m_lastKnownMtime = info.lastModified();
      m_index.rebuild(doc);
    }
    armFileWatcher(path.toStdString());
  });
}


// ---------------------------------------------------------------------------
// native sculpting: vertex / edge / face (2026.2 free functions)
// ---------------------------------------------------------------------------

QJsonObject McpServerBridge::geoGetBrushTopology(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());
  (void)m;

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  if (ids.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "ids[] is required"};
  }

  auto missing = std::vector<std::string>{};
  const auto nodes = m_index.resolve(ids, missing);

  auto brushes = QJsonArray{};
  for (auto* node : nodes)
  {
    auto* brushNode = dynamic_cast<mdl::BrushNode*>(node);
    if (brushNode == nullptr)
    {
      continue;
    }
    const auto& geometry = brushNode->brush();

    auto vertices = std::vector<vm::vec3d>{};
    for (const auto* vertex : geometry.vertices())
    {
      vertices.push_back(vertex->position());
    }

    const auto indexOf = [&](const vm::vec3d& q) -> int {
      for (size_t i = 0; i < vertices.size(); ++i)
      {
        if (vm::length(vertices[i] - q) < 1e-3)
        {
          return static_cast<int>(i);
        }
      }
      return -1;
    };

    auto vertexJson = QJsonArray{};
    for (const auto& v : vertices)
    {
      vertexJson.append(mcp::vec3ToJson(v));
    }

    auto faces = QJsonArray{};
    auto edges = QJsonArray{};
    auto seenEdges = std::set<std::pair<int, int>>{};

    for (size_t fi = 0; fi < geometry.faceCount(); ++fi)
    {
      const auto& face = geometry.face(fi);
      const auto positions = face.vertexPositions();

      auto indices = QJsonArray{};
      for (const auto& point : positions)
      {
        const auto index = indexOf(point);
        indices.append(index);
      }

      auto faceJson = QJsonObject{};
      faceJson.insert(QLatin1String{"index"}, static_cast<int>(fi));
      faceJson.insert(
        QLatin1String{"material"}, QString::fromStdString(face.attributes().materialName()));
      faceJson.insert(
        QLatin1String{"normal"},
        mcp::vec3ToJson(vm::vec3d{face.normal().x(), face.normal().y(), face.normal().z()}));
      faceJson.insert(
        QLatin1String{"distance"}, static_cast<double>(face.boundary().distance));
      faceJson.insert(QLatin1String{"vertex_indices"}, indices);
      faceJson.insert(
        QLatin1String{"uv_offset"},
        QJsonArray{
          static_cast<double>(face.attributes().offset().x()),
          static_cast<double>(face.attributes().offset().y())});
      faceJson.insert(
        QLatin1String{"uv_scale"},
        QJsonArray{
          static_cast<double>(face.attributes().scale().x()),
          static_cast<double>(face.attributes().scale().y())});
      faceJson.insert(
        QLatin1String{"uv_rotation"}, static_cast<double>(face.attributes().rotation()));

      auto points = QJsonArray{};
      for (const auto& point : positions)
      {
        points.append(mcp::vec3ToJson(point));
      }
      faceJson.insert(QLatin1String{"plane_points"}, points);
      faces.append(faceJson);

      const auto count = indices.size();
      for (int i = 0; i < count; ++i)
      {
        const auto a = indices.at(i).toInt();
        const auto b = indices.at((i + 1) % count).toInt();
        if (a < 0 || b < 0)
        {
          continue;
        }
        const auto key = std::minmax(a, b);
        if (seenEdges.insert({key.first, key.second}).second)
        {
          edges.append(QJsonArray{a, b});
        }
      }
    }

    auto entry = QJsonObject{};
    entry.insert(QLatin1String{"id"}, QString::fromStdString(m_index.idOf(brushNode)));
    entry.insert(QLatin1String{"vertices"}, vertexJson);
    entry.insert(QLatin1String{"faces"}, faces);
    entry.insert(QLatin1String{"edges"}, edges);
    brushes.append(entry);
  }

  auto result = QJsonObject{};
  result.insert(QLatin1String{"brushes"}, brushes);
  result.insert(QLatin1String{"missing"}, jsonFromStringVector(missing));
  return result;
}

QJsonObject McpServerBridge::geoMoveVertices(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  auto missing = std::vector<std::string>{};
  selectOnly(m_index.resolve(ids, missing));

  auto positions = std::vector<vm::vec3d>{};
  for (const auto& value : p.value(QLatin1String{"vertices"}).toArray())
  {
    positions.push_back(mcp::vec3FromJson(value));
  }
  if (positions.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "vertices[] is required"};
  }

  auto delta = mcp::vec3FromJson(p.value(QLatin1String{"delta"}));
  const auto grid = static_cast<int>(mcp::numberFromJson(p.value(QLatin1String{"grid"})));
  if (grid > 1)
  {
    delta = snapVector(delta, grid);
  }

  auto transaction = mdl::Transaction{m, "MCP geo.move_vertices"};
  const auto outcome =
    mdl::transformVertices(m, positions, vm::translation_matrix(delta));
  transaction.finish(outcome.success);

  if (!outcome.success)
  {
    throw McpError{
      mcp::ErrorCode::OperationFailed,
      "vertex move produced a non-convex or degenerate brush and was rolled back"};
  }

  return QJsonObject{
    {QLatin1String{"moved"}, static_cast<int>(positions.size())},
    {QLatin1String{"hasRemainingVertices"}, outcome.hasRemainingVertices}};
}

QJsonObject McpServerBridge::geoMoveEdges(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  auto missing = std::vector<std::string>{};
  selectOnly(m_index.resolve(ids, missing));

  auto edges = std::vector<vm::segment3d>{};
  for (const auto& value : p.value(QLatin1String{"edges"}).toArray())
  {
    const auto pair = value.toArray();
    if (pair.size() < 2)
    {
      continue;
    }
    edges.emplace_back(mcp::vec3FromJson(pair.at(0)), mcp::vec3FromJson(pair.at(1)));
  }
  if (edges.empty())
  {
    throw McpError{
      mcp::ErrorCode::InvalidParams, "edges[] must be [[p1, p2], ...]"};
  }

  auto delta = mcp::vec3FromJson(p.value(QLatin1String{"delta"}));
  const auto grid = static_cast<int>(mcp::numberFromJson(p.value(QLatin1String{"grid"})));
  if (grid > 1)
  {
    delta = snapVector(delta, grid);
  }

  auto transaction = mdl::Transaction{m, "MCP geo.move_edges"};
  const auto ok = mdl::transformEdges(m, edges, vm::translation_matrix(delta));
  transaction.finish(ok);
  if (!ok)
  {
    throw McpError{mcp::ErrorCode::OperationFailed, "edge move failed and was rolled back"};
  }

  return QJsonObject{{QLatin1String{"moved"}, static_cast<int>(edges.size())}};
}

QJsonObject McpServerBridge::geoMoveFaces(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  auto missing = std::vector<std::string>{};
  selectOnly(m_index.resolve(ids, missing));

  auto faces = std::vector<vm::polygon3d>{};
  for (const auto& value : p.value(QLatin1String{"faces"}).toArray())
  {
    auto points = std::vector<vm::vec3d>{};
    for (const auto& point : value.toArray())
    {
      points.push_back(mcp::vec3FromJson(point));
    }
    if (points.size() >= 3)
    {
      faces.emplace_back(std::move(points));
    }
  }
  if (faces.empty())
  {
    throw McpError{
      mcp::ErrorCode::InvalidParams, "faces[] must be [[p1, p2, p3, ...], ...]"};
  }

  const auto delta = mcp::vec3FromJson(p.value(QLatin1String{"delta"}));

  auto transaction = mdl::Transaction{m, "MCP geo.move_faces"};
  const auto ok = mdl::transformFaces(m, faces, vm::translation_matrix(delta));
  transaction.finish(ok);
  if (!ok)
  {
    throw McpError{mcp::ErrorCode::OperationFailed, "face move failed and was rolled back"};
  }

  return QJsonObject{{QLatin1String{"moved"}, static_cast<int>(faces.size())}};
}

QJsonObject McpServerBridge::geoExtrudeFaces(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  auto missing = std::vector<std::string>{};
  const auto nodes = m_index.resolve(ids, missing);

  auto faces = std::vector<vm::polygon3d>{};
  auto handles = std::vector<mdl::BrushFaceHandle>{};

  for (const auto& value : p.value(QLatin1String{"faces"}).toArray())
  {
    auto points = std::vector<vm::vec3d>{};
    for (const auto& point : value.toArray())
    {
      points.push_back(mcp::vec3FromJson(point));
    }
    if (points.size() >= 3)
    {
      faces.emplace_back(vm::polygon3d{points});
    }
  }

  if (faces.empty() && !nodes.empty())
  {
    // no explicit faces: extrude every face of the targeted brushes
    for (auto* node : nodes)
    {
      if (auto* brushNode = dynamic_cast<mdl::BrushNode*>(node))
      {
        for (size_t i = 0; i < brushNode->brush().faceCount(); ++i)
        {
          faces.push_back(brushNode->brush().face(i).polygon());
          handles.emplace_back(brushNode, i);
        }
      }
    }
  }

  if (faces.empty())
  {
    throw McpError{
      mcp::ErrorCode::InvalidParams, "provide faces[] or ids[] of brushes to extrude"};
  }

  const auto delta = mcp::vec3FromJson(
    p.value(QLatin1String{"delta"}),
    vm::vec3d{0, 0, mcp::numberFromJson(p.value(QLatin1String{"distance"}), 16.0)});

  selectOnly(nodes);
  auto transaction = mdl::Transaction{m, "MCP geo.extrude_faces"};
  const auto ok = mdl::extrudeBrushes(m, faces, delta);
  transaction.finish(ok);
  if (!ok)
  {
    throw McpError{mcp::ErrorCode::OperationFailed, "face extrusion failed"};
  }

  auto created = std::vector<mdl::Node*>{};
  for (auto* node : m.selection().nodes)
  {
    created.push_back(node);
  }

  return QJsonObject{
    {QLatin1String{"extruded"}, static_cast<int>(faces.size())},
    {QLatin1String{"ids"}, jsonFromStringVector(stampIds(created))}};
}

QJsonObject McpServerBridge::geoAddVertex(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  auto missing = std::vector<std::string>{};
  selectOnly(m_index.resolve(ids, missing));

  const auto position = mcp::vec3FromJson(p.value(QLatin1String{"position"}));

  auto transaction = mdl::Transaction{m, "MCP geo.add_vertex"};
  const auto ok = mdl::addVertex(m, position);
  transaction.finish(ok);
  if (!ok)
  {
    throw McpError{mcp::ErrorCode::OperationFailed, "could not add the vertex"};
  }
  return QJsonObject{{QLatin1String{"added"}, true}};
}

QJsonObject McpServerBridge::geoRemoveVertices(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  auto missing = std::vector<std::string>{};
  selectOnly(m_index.resolve(ids, missing));

  auto positions = std::vector<vm::vec3d>{};
  for (const auto& value : p.value(QLatin1String{"vertices"}).toArray())
  {
    positions.push_back(mcp::vec3FromJson(value));
  }
  if (positions.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "vertices[] is required"};
  }

  auto transaction = mdl::Transaction{m, "MCP geo.remove_vertices"};
  const auto ok = mdl::removeVertices(m, "Remove vertices", positions);
  transaction.finish(ok);
  if (!ok)
  {
    throw McpError{mcp::ErrorCode::OperationFailed, "could not remove the vertices"};
  }
  return QJsonObject{{QLatin1String{"removed"}, static_cast<int>(positions.size())}};
}

QJsonObject McpServerBridge::geoSnapVertices(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  if (!ids.empty())
  {
    auto missing = std::vector<std::string>{};
    selectOnly(m_index.resolve(ids, missing));
  }

  const auto grid = mcp::numberFromJson(p.value(QLatin1String{"grid"}), 1.0);
  if (grid <= 0.0)
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "grid must be > 0"};
  }

  auto transaction = mdl::Transaction{m, "MCP geo.snap_vertices"};
  const auto ok = mdl::snapVertices(m, grid);
  transaction.finish(ok);

  return QJsonObject{
    {QLatin1String{"snapped"}, ok}, {QLatin1String{"grid"}, grid}};
}

QJsonObject McpServerBridge::geoShear(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  if (!ids.empty())
  {
    auto missing = std::vector<std::string>{};
    selectOnly(m_index.resolve(ids, missing));
  }
  if (m.selection().nodes.empty())
  {
    throw McpError{mcp::ErrorCode::NoSelection, "nothing selected"};
  }

  const auto& bounds = m.selectionBounds();
  if (!bounds)
  {
    throw McpError{mcp::ErrorCode::NoSelection, "selection has no bounds"};
  }

  const auto side = mcp::vec3FromJson(p.value(QLatin1String{"side"}), vm::vec3d{1, 0, 0});
  const auto delta = mcp::vec3FromJson(p.value(QLatin1String{"delta"}));

  auto transaction = mdl::Transaction{m, "MCP geo.shear"};
  const auto ok = mdl::shearSelection(m, *bounds, side, delta);
  transaction.finish(ok);
  if (!ok)
  {
    throw McpError{mcp::ErrorCode::OperationFailed, "shear failed"};
  }

  auto nodes = std::vector<mdl::Node*>{m.selection().nodes};
  return QJsonObject{{QLatin1String{"ids"}, jsonFromStringVector(stampIds(nodes))}};
}

QJsonObject McpServerBridge::geoFlip(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto ids = mcp::stringArrayFromJson(p.value(QLatin1String{"ids"}));
  if (!ids.empty())
  {
    auto missing = std::vector<std::string>{};
    selectOnly(m_index.resolve(ids, missing));
  }
  if (m.selection().nodes.empty())
  {
    throw McpError{mcp::ErrorCode::NoSelection, "nothing selected"};
  }

  const auto axisName = mcp::stringFromJson(p.value(QLatin1String{"axis"}), "x");
  const auto axis = axisName == "y" ? vm::axis::y
                    : axisName == "z" ? vm::axis::z
                                      : vm::axis::x;

  auto center = vm::vec3d{0, 0, 0};
  if (const auto& bounds = m.selectionBounds())
  {
    center = bounds->center();
  }

  auto transaction = mdl::Transaction{m, "MCP geo.flip " + axisName};
  const auto ok = mdl::flipSelection(m, center, axis);
  transaction.finish(ok);
  if (!ok)
  {
    throw McpError{mcp::ErrorCode::OperationFailed, "flip failed"};
  }

  auto nodes = std::vector<mdl::Node*>{m.selection().nodes};
  return QJsonObject{{QLatin1String{"ids"}, jsonFromStringVector(stampIds(nodes))}};
}

QJsonObject McpServerBridge::geoDuplicateAndTransform(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  if (m.selection().nodes.empty())
  {
    throw McpError{
      mcp::ErrorCode::NoSelection,
      "select the source geometry (ids[]) before radial duplication"};
  }

  const auto count = static_cast<int>(mcp::numberFromJson(p.value(QLatin1String{"count"}), 1.0));
  if (count < 1 || count > 512)
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "count must be 1..512"};
  }

  const auto translate = mcp::vec3FromJson(p.value(QLatin1String{"translate"}));

  auto rotate = false;
  auto center = vm::vec3d{0, 0, 0};
  auto axis = vm::vec3d{0, 0, 1};
  auto degrees = 0.0;
  if (const auto value = p.value(QLatin1String{"rotate"}); value.isObject())
  {
    const auto rotation = value.toObject();
    center = mcp::vec3FromJson(rotation.value(QLatin1String{"center"}));
    axis = mcp::vec3FromJson(rotation.value(QLatin1String{"axis"}), vm::vec3d{0, 0, 1});
    degrees = mcp::numberFromJson(rotation.value(QLatin1String{"degrees"}), 0.0);
    rotate = degrees != 0.0;
  }

  auto transaction = mdl::Transaction{m, "MCP geo.duplicate_and_transform"};
  auto created = std::vector<mdl::Node*>{};

  for (int i = 0; i < count; ++i)
  {
    mdl::duplicateSelectedNodes(m);
    if (rotate)
    {
      mdl::rotateSelection(m, center, vm::normalize(axis), degrees * Pi / 180.0);
    }
    if (translate != vm::vec3d{0, 0, 0})
    {
      mdl::translateSelection(m, translate);
    }
    for (auto* node : m.selection().nodes)
    {
      created.push_back(node);
    }
  }
  transaction.commit();

  return QJsonObject{
    {QLatin1String{"copies"}, count},
    {QLatin1String{"ids"}, jsonFromStringVector(stampIds(created))}};
}

// ---------------------------------------------------------------------------
// native UV tools
// ---------------------------------------------------------------------------

QJsonObject McpServerBridge::texFitTexture(const QJsonObject& p)
{
  auto& m = map();

  const auto direction = mcp::stringFromJson(p.value(QLatin1String{"direction"}), "both");
  const auto fitDirection = direction == "horizontal" ? mdl::UvFitDirection::Horizontal
                             : direction == "vertical" ? mdl::UvFitDirection::Vertical
                                                       : mdl::UvFitDirection::Horizontal;

  auto transaction = mdl::Transaction{m, "MCP tex.fit_texture"};
  mdl::fitUV(m, fitDirection, mdl::UvPolicy::best, mdl::UvFitMode::fitToFace);
  transaction.commit();

  return QJsonObject{
    {QLatin1String{"fitted"}, true},
    {QLatin1String{"direction"}, QString::fromStdString(direction)}};
}

QJsonObject McpServerBridge::texAlignToFace(const QJsonObject& p)
{
  (void)p;
  auto& m = map();
  auto transaction = mdl::Transaction{m, "MCP tex.align_to_face"};
  mdl::alignUV(m, mdl::UvPolicy::best);
  transaction.commit();
  return QJsonObject{{QLatin1String{"aligned"}, QLatin1String{"face"}}};
}

QJsonObject McpServerBridge::texAlignToWorld(const QJsonObject& p)
{
  (void)p;
  auto& m = map();

  auto update = mdl::UpdateBrushFaceAttributes{};
  update.axis = mdl::AxisOp{mdl::ToParaxial{}};

  auto transaction = mdl::Transaction{m, "MCP tex.align_to_world"};
  const auto ok = mdl::setBrushFaceAttributes(m, update);
  transaction.finish(ok);

  return QJsonObject{
    {QLatin1String{"aligned"}, QLatin1String{"world"}}, {QLatin1String{"applied"}, ok}};
}

QJsonObject McpServerBridge::texCopyUvBetweenFaces(const QJsonObject& p)
{
  auto& m = map();
  m_index.rebuild(document());

  const auto sourceId = mcp::stringFromJson(p.value(QLatin1String{"source_id"}));
  const auto sourceFaceIndex =
    static_cast<size_t>(mcp::numberFromJson(p.value(QLatin1String{"source_face"}), 0.0));

  auto* sourceNode = m_index.find(sourceId);
  auto* sourceBrush = dynamic_cast<mdl::BrushNode*>(sourceNode);
  if (sourceBrush == nullptr || sourceFaceIndex >= sourceBrush->brush().faceCount())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "source_id / source_face not found"};
  }
  const auto& sourceFace = sourceBrush->brush().face(sourceFaceIndex);
  auto snapshot = sourceFace.takeUVCoordSystemSnapshot();

  auto handles = std::vector<mdl::BrushFaceHandle>{};
  const auto targetIds = mcp::stringArrayFromJson(p.value(QLatin1String{"target_ids"}));
  for (const auto& id : targetIds)
  {
    auto* node = m_index.find(id);
    auto* brushNode = dynamic_cast<mdl::BrushNode*>(node);
    if (brushNode == nullptr)
    {
      continue;
    }
    for (size_t i = 0; i < brushNode->brush().faceCount(); ++i)
    {
      handles.emplace_back(brushNode, i);
    }
  }

  if (handles.empty())
  {
    handles = m.selection().brushFaces;
  }
  if (handles.empty())
  {
    throw McpError{
      mcp::ErrorCode::NoSelection,
      "provide target_ids[] or select the target faces first"};
  }

  auto transaction = mdl::Transaction{m, "MCP tex.copy_uv"};
  mdl::selectBrushFaces(m, handles);
  const auto ok = mdl::copyUV(
    m,
    *snapshot,
    sourceFace.attributes(),
    sourceFace.boundary(),
    mdl::WrapStyle::Projection);
  transaction.finish(ok);
  if (!ok)
  {
    throw McpError{mcp::ErrorCode::OperationFailed, "copyUV failed"};
  }

  return QJsonObject{
    {QLatin1String{"faces"}, static_cast<int>(handles.size())},
    {QLatin1String{"source"}, QString::fromStdString(sourceId)}};
}

// ---------------------------------------------------------------------------
// native spatial selection
// ---------------------------------------------------------------------------

QJsonObject McpServerBridge::selSelectTouching(const QJsonObject& p)
{
  auto& m = map();
  if (m.selection().nodes.empty())
  {
    throw McpError{mcp::ErrorCode::NoSelection, "select a seed brush first"};
  }

  auto transaction = mdl::Transaction{m, "MCP sel.select_touching"};
  mdl::selectTouchingNodes(m, mcp::boolFromJson(p.value(QLatin1String{"delete"}), false));
  transaction.commit();

  m_index.rebuild(document());
  auto nodes = std::vector<mdl::Node*>{m.selection().nodes};

  return QJsonObject{
    {QLatin1String{"selected"}, static_cast<int>(nodes.size())},
    {QLatin1String{"ids"}, jsonFromStringVector(stampIds(nodes))}};
}

QJsonObject McpServerBridge::selSelectInside(const QJsonObject& p)
{
  auto& m = map();
  if (m.selection().nodes.empty())
  {
    throw McpError{
      mcp::ErrorCode::NoSelection, "select the enclosing brushes first"};
  }

  auto transaction = mdl::Transaction{m, "MCP sel.select_inside"};
  mdl::selectContainedNodes(m, mcp::boolFromJson(p.value(QLatin1String{"delete"}), false));
  transaction.commit();

  m_index.rebuild(document());
  auto nodes = std::vector<mdl::Node*>{m.selection().nodes};

  return QJsonObject{
    {QLatin1String{"selected"}, static_cast<int>(nodes.size())},
    {QLatin1String{"ids"}, jsonFromStringVector(stampIds(nodes))}};
}

// ---------------------------------------------------------------------------
// visibility / isolation / render mode
// ---------------------------------------------------------------------------

QJsonObject McpServerBridge::viewIsolateSelection(const QJsonObject& p)
{
  (void)p;
  auto& m = map();
  if (m.selection().nodes.empty())
  {
    throw McpError{mcp::ErrorCode::NoSelection, "nothing selected to isolate"};
  }
  auto transaction = mdl::Transaction{m, "MCP view.isolate_selection"};
  mdl::isolateSelectedNodes(m);
  transaction.commit();
  return QJsonObject{{QLatin1String{"isolated"}, static_cast<int>(m.selection().nodes.size())}};
}

QJsonObject McpServerBridge::viewHideSelection(const QJsonObject& p)
{
  (void)p;
  auto& m = map();
  if (m.selection().nodes.empty())
  {
    throw McpError{mcp::ErrorCode::NoSelection, "nothing selected to hide"};
  }
  auto transaction = mdl::Transaction{m, "MCP view.hide_selection"};
  mdl::hideSelectedNodes(m);
  transaction.commit();
  return QJsonObject{{QLatin1String{"hidden"}, true}};
}

QJsonObject McpServerBridge::viewUnhideAll(const QJsonObject& p)
{
  (void)p;
  auto& m = map();
  auto transaction = mdl::Transaction{m, "MCP view.unhide_all"};
  mdl::showAllNodes(m);
  transaction.commit();
  return QJsonObject{{QLatin1String{"visible"}, QLatin1String{"all"}}};
}

QJsonObject McpServerBridge::viewSetRenderMode(const QJsonObject& p)
{
  auto& prefs = PreferenceManager::instance();
  auto applied = QJsonObject{};

  if (p.contains(QLatin1String{"edges"}))
  {
    const auto value = mcp::boolFromJson(p.value(QLatin1String{"edges"}));
    prefs.set(Preferences::ShowEdges, value);
    applied.insert(QLatin1String{"edges"}, value);
  }
  if (p.contains(QLatin1String{"shade_faces"}))
  {
    const auto value = mcp::boolFromJson(p.value(QLatin1String{"shade_faces"}));
    prefs.set(Preferences::ShadeFaces, value);
    applied.insert(QLatin1String{"shade_faces"}, value);
  }

  if (auto* view = requireWindow().findChild<MapView3D*>())
  {
    static_cast<MapViewBase*>(view)->update();
  }

  return applied;
}


QJsonObject McpServerBridge::docSetMods(const QJsonObject& p)
{
  auto& m = map();

  const auto mods = mcp::stringArrayFromJson(p.value(QLatin1String{"mods"}));
  if (mods.empty())
  {
    throw McpError{mcp::ErrorCode::InvalidParams, "mods[] is required"};
  }

  auto transaction = mdl::Transaction{m, "MCP doc.set_mods"};
  mdl::setEnabledMods(m, mods);

  // WADs only resolve once the mod directories are mounted, so update the
  // worldspawn "wad" property and reload material collections after mods change.
  const auto wads = mcp::stringArrayFromJson(p.value(QLatin1String{"wads"}));
  if (!wads.empty())
  {
    auto joined = std::string{};
    for (size_t i = 0; i < wads.size(); ++i)
    {
      if (i > 0)
      {
        joined += ';';
      }
      joined += wads[i];
    }
    mdl::deselectAll(m);
    mdl::setEntityProperty(m, mdl::EntityPropertyKeys::Wad, joined);
  }
  mdl::reloadMaterialCollections(m);
  transaction.commit();

  return QJsonObject{
    {QLatin1String{"mods"}, jsonFromStringVector(mods)},
    {QLatin1String{"materials"},
     static_cast<int>(m.materialManager().materials().size())},
    {QLatin1String{"collections"},
     static_cast<int>(mdl::enabledMaterialCollections(m).size())}};
}


} // namespace tb::ui
